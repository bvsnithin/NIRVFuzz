#!/usr/bin/env python3
import os
import sys
import json
import random
import subprocess
import time
import shutil

SIM_CLEAN = "../sim/Vtop"
SIM_BUGGY = "../sim/Vtop_buggy"
SEED_DIR = "../seed_corpus"
CRASH_DIR = "crashes"

class Seed:
    def __init__(self, filename, content):
        self.filename = filename
        self.content = bytearray(content)
        self.fitness = 0  # Toggle count

def setup_dirs():
    if not os.path.exists(CRASH_DIR):
        os.makedirs(CRASH_DIR)
    if not os.path.exists(SEED_DIR):
        os.makedirs(SEED_DIR)
        # Create a dummy seed if empty
        with open(os.path.join(SEED_DIR, "seed1.bin"), "wb") as f:
            # 00000093 : addi x1, x0, 0
            # 00108113 : addi x2, x1, 1
            # 00208193 : addi x3, x1, 2
            # 00208233 : add x4, x1, x2
            # 00100073 : ebreak
            f.write(bytes.fromhex("9300000013811000938120003382200073001000"))

def load_seeds():
    seeds = []
    for f in os.listdir(SEED_DIR):
        if f.endswith(".bin"):
            with open(os.path.join(SEED_DIR, f), "rb") as file:
                seeds.append(Seed(f, file.read()))
    return seeds

def mutate(seed_content):
    # Genetic algorithm mutation: randomly flip a few bits or bytes
    mutated = bytearray(seed_content)
    num_mutations = random.randint(1, max(1, len(mutated) // 4))
    for _ in range(num_mutations):
        idx = random.randint(0, len(mutated) - 1)
        if random.random() < 0.5:
            # Byte flip
            mutated[idx] = random.randint(0, 255)
        else:
            # Bit flip
            mutated[idx] ^= (1 << random.randint(0, 7))
    return mutated

def run_sim(executable, binary_path):
    try:
        result = subprocess.run([executable, binary_path], capture_output=True, text=True, timeout=5)
        # Find the JSON part
        stdout = result.stdout
        start_idx = stdout.find("{")
        end_idx = stdout.rfind("}")
        if start_idx != -1 and end_idx != -1:
            json_str = stdout[start_idx:end_idx+1]
            return json.loads(json_str)
    except Exception as e:
        print(f"Error running {executable}: {e}")
    return None

def main():
    setup_dirs()
    population = load_seeds()
    if not population:
        print("No seeds found!")
        return

    print(f"Loaded {len(population)} initial seeds.")
    
    generation = 0
    max_fitness_seen = 0
    
    while True:
        generation += 1
        # Pick a parent based on fitness (roulette wheel selection or just random if all 0)
        total_fitness = sum(s.fitness for s in population)
        if total_fitness == 0:
            parent = random.choice(population)
        else:
            r = random.uniform(0, total_fitness)
            upto = 0
            for s in population:
                if upto + s.fitness >= r:
                    parent = s
                    break
                upto += s.fitness

        # Mutate to create child
        child_content = mutate(parent.content)
        child_path = f"tmp_child_{os.getpid()}.bin"
        with open(child_path, "wb") as f:
            f.write(child_content)

        # Run clean simulation
        clean_res = run_sim(SIM_CLEAN, child_path)
        if clean_res is None:
            continue
            
        toggle_count = clean_res.get("toggle_count", 0)
        crc_clean = clean_res.get("crc_out", 0)
        golden_crc = clean_res.get("golden_crc", 0)
        
        # Differential Testing: Run buggy simulation
        buggy_res = run_sim(SIM_BUGGY, child_path)
        if buggy_res is not None:
            crc_buggy = buggy_res.get("crc_out", 0)
            
            if crc_clean != crc_buggy:
                print(f"\n[!] DIVERGENCE DETECTED in generation {generation}!")
                print(f"    Clean CRC:  {hex(crc_clean)}")
                print(f"    Buggy CRC:  {hex(crc_buggy)}")
                
                # Save crashing input
                crash_name = os.path.join(CRASH_DIR, f"crash_{generation}.bin")
                shutil.copy(child_path, crash_name)
                print(f"    Saved crash to {crash_name}")
                print(f"    VCD trace: {SIM_CLEAN} {crash_name} trace_clean.vcd && {SIM_BUGGY} {crash_name} trace_buggy.vcd")
                # Continue fuzzing — do not break, accumulate all diverging inputs

        # Genetic Algorithm: Keep child if it explored new behavior (higher toggle count)
        if toggle_count > max_fitness_seen:
            max_fitness_seen = toggle_count
            print(f"Gen {generation}: New max toggle count: {toggle_count}")
            # Add child to population
            new_seed = Seed(f"seed_gen{generation}.bin", child_content)
            new_seed.fitness = toggle_count
            population.append(new_seed)
            # Save it
            shutil.copy(child_path, os.path.join(SEED_DIR, new_seed.filename))

            # Maintain population size
            if len(population) > 50:
                population.sort(key=lambda x: x.fitness, reverse=True)
                removed = population.pop()
                try:
                    os.remove(os.path.join(SEED_DIR, removed.filename))
                except:
                    pass

        # Cleanup tmp
        if os.path.exists(child_path):
            os.remove(child_path)

        if generation % 100 == 0:
            sys.stdout.write(f"\rGeneration {generation} | Population {len(population)} | Max Toggle {max_fitness_seen} ...")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
