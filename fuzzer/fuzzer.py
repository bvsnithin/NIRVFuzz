#!/usr/bin/env python3
"""
NIRVFuzz -- Coverage-guided genetic fuzzer for RISC-V processor cores.

Generates and mutates valid RV32IM instruction sequences, runs them through
Verilator simulations of a clean and buggy PicoRV32, and uses toggle-count
feedback to guide the search toward inputs that expose hardware bugs.
"""
import os
import sys
import json
import struct
import random
import subprocess
import shutil

SIM_CLEAN = "../sim/Vtop"
SIM_BUGGY = "../sim/Vtop_buggy"
SEED_DIR  = "../seed_corpus"
CRASH_DIR = "crashes"

EBREAK       = 0x00100073
MAX_PROG_LEN = 32    # max non-EBREAK instructions per program
MIN_PROG_LEN = 1     # min non-EBREAK instructions per program
DATA_BASE    = 0x200 # safe data area base address (above max code footprint of ~132 bytes)
DATA_TOP     = 0x7FC # top of safe data area (stays within positive I-type imm range)


# =============================================================================
# RV32IM Instruction Encoders
# =============================================================================

def encode_r(opcode, funct3, funct7, rd, rs1, rs2):
    return ((funct7 & 0x7F) << 25) | ((rs2 & 0x1F) << 20) | \
           ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | \
           ((rd  & 0x1F) << 7)  | (opcode & 0x7F)

def encode_i(opcode, funct3, rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | ((rs1 & 0x1F) << 15) | \
           ((funct3 & 0x7) << 12) | ((rd & 0x1F) << 7) | (opcode & 0x7F)

def encode_s(funct3, rs1, rs2, imm):
    imm12 = imm & 0xFFF
    return (((imm12 >> 5) & 0x7F) << 25) | ((rs2 & 0x1F) << 20) | \
           ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | \
           ((imm12 & 0x1F) << 7) | 0x23

def encode_b(funct3, rs1, rs2, imm):
    """imm is a signed byte offset (must be even, forward-only so positive)."""
    imm &= 0x1FFE  # 13-bit, bit 0 always 0
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | \
           ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) | \
           ((funct3 & 0x7) << 12) | (((imm >> 1) & 0xF) << 8) | \
           (((imm >> 11) & 1) << 7) | 0x63

def encode_u(opcode, rd, imm20):
    return ((imm20 & 0xFFFFF) << 12) | ((rd & 0x1F) << 7) | (opcode & 0x7F)


# =============================================================================
# Random Instruction Generator
# =============================================================================

# R-type ALU: (funct3, funct7) pairs
ALU_R_OPS = [
    (0x0, 0x00),  # ADD
    (0x0, 0x20),  # SUB
    (0x1, 0x00),  # SLL
    (0x2, 0x00),  # SLT
    (0x3, 0x00),  # SLTU
    (0x4, 0x00),  # XOR
    (0x5, 0x00),  # SRL
    (0x5, 0x20),  # SRA
    (0x6, 0x00),  # OR
    (0x7, 0x00),  # AND
]

# I-type ALU funct3 values (excluding shifts to keep encodings simple)
ALU_I_FUNCT3 = [0, 2, 3, 4, 6, 7]  # ADDI, SLTI, SLTIU, XORI, ORI, ANDI

# RV32M funct3 values (opcode=0x33, funct7=0x01)
MUL_OPS = [0, 1, 2, 3, 4, 5, 6, 7]  # MUL, MULH, MULHSU, MULHU, DIV, DIVU, REM, REMU

# Branch funct3 values
BR_FUNCT3 = [0, 1, 4, 5, 6, 7]  # BEQ, BNE, BLT, BGE, BLTU, BGEU

# Load funct3 and byte alignment: (funct3, alignment)
LOAD_OPS  = [(0, 1), (1, 2), (2, 4), (4, 1), (5, 2)]  # LB, LH, LW, LBU, LHU
# Store funct3 and byte alignment
STORE_OPS = [(0, 1), (1, 2), (2, 4)]                  # SB, SH, SW


def rand_rd():  return random.randint(1, 15)   # x1-x15: avoid hardwired x0
def rand_rs():  return random.randint(0, 15)   # x0-x15: x0 reads as zero
def rand_imm(): return random.randint(-2048, 2047)

def rand_data_addr(alignment):
    """Random address in the safe data area, aligned to 'alignment' bytes."""
    lo = (DATA_BASE + alignment - 1) // alignment
    hi = DATA_TOP // alignment
    return random.randint(lo, hi) * alignment

def random_instruction(prog_len=MAX_PROG_LEN, insert_pos=0):
    """
    Generate one random valid RV32IM instruction.
    prog_len and insert_pos are used to bound branch offsets so branches
    always jump forward within the program (never past EBREAK).
    """
    # Weights: alu_i appears twice to increase frequency for simple programs
    category = random.choice([
        'alu_r', 'alu_i', 'alu_i', 'lui',
        'mul', 'load', 'store', 'branch'
    ])

    rd, rs1, rs2 = rand_rd(), rand_rs(), rand_rs()
    imm = rand_imm()

    if category == 'alu_r':
        f3, f7 = random.choice(ALU_R_OPS)
        return encode_r(0x33, f3, f7, rd, rs1, rs2)

    elif category == 'alu_i':
        f3 = random.choice(ALU_I_FUNCT3)
        return encode_i(0x13, f3, rd, rs1, imm)

    elif category == 'lui':
        return encode_u(0x37, rd, random.randint(0, 0xFFFFF))

    elif category == 'mul':
        # RV32M: funct7=0x01, opcode=0x33
        f3 = random.choice(MUL_OPS)
        return encode_r(0x33, f3, 0x01, rd, rs1, rs2)

    elif category == 'load':
        f3, align = random.choice(LOAD_OPS)
        addr = rand_data_addr(align)
        return encode_i(0x03, f3, rd, 0, addr)  # base = x0

    elif category == 'store':
        f3, align = random.choice(STORE_OPS)
        addr = rand_data_addr(align)
        return encode_s(f3, 0, rs2, addr)        # base = x0

    elif category == 'branch':
        f3 = random.choice(BR_FUNCT3)
        # Forward-only: skip 1 to 3 instructions, but never jump past EBREAK
        instructions_after = prog_len - insert_pos  # includes EBREAK
        max_skip = min(3, instructions_after - 1)
        if max_skip < 1:
            # No room to branch forward: fall back to ADDI (NOP-like)
            return encode_i(0x13, 0, rd, rs1, imm)
        offset = random.randint(1, max_skip) * 4
        return encode_b(f3, rs1, rs2, offset)

    # Fallback: NOP (ADDI x0, x0, 0)
    return encode_i(0x13, 0, 0, 0, 0)


# =============================================================================
# Program Serialization
# =============================================================================

def program_to_bytes(program):
    """Encode a list of 32-bit instruction words to little-endian bytes."""
    return b''.join(struct.pack('<I', instr) for instr in program)

def bytes_to_program(data):
    """Decode raw bytes into a list of 32-bit instruction words."""
    words = []
    for i in range(0, len(data) - 3, 4):
        words.append(struct.unpack('<I', data[i:i+4])[0])
    # Guarantee program ends with EBREAK
    if not words or words[-1] != EBREAK:
        words.append(EBREAK)
    return words


# =============================================================================
# Instruction-Aware Mutator
# =============================================================================

def mutate(seed_bytes):
    """
    Apply one of four instruction-level mutation operations to a program:
      - replace  (40%): replace a random instruction with a new valid one
      - insert   (30%): insert a new valid instruction (grows program)
      - delete   (20%): remove a random instruction (shrinks program)
      - swap     (10%): swap two instructions
    Always preserves EBREAK at the end.
    Returns mutated bytes.
    """
    prog = bytes_to_program(seed_bytes)
    # Split into body (everything before EBREAK) and EBREAK terminator
    body = [instr for instr in prog if instr != EBREAK]

    op = random.random()

    if op < 0.40 and body:
        # Replace a random instruction with a new valid one
        idx = random.randint(0, len(body) - 1)
        body[idx] = random_instruction(len(body), idx)

    elif op < 0.70 and len(body) < MAX_PROG_LEN:
        # Insert a new instruction at a random position
        idx = random.randint(0, len(body))
        new_instr = random_instruction(len(body) + 1, idx)
        body.insert(idx, new_instr)

    elif op < 0.90 and len(body) > MIN_PROG_LEN:
        # Delete a random instruction
        idx = random.randint(0, len(body) - 1)
        body.pop(idx)

    elif len(body) >= 2:
        # Swap two random instructions
        i, j = random.sample(range(len(body)), 2)
        body[i], body[j] = body[j], body[i]

    # Reattach EBREAK
    prog = body + [EBREAK]
    return program_to_bytes(prog)


# =============================================================================
# Seed Corpus
# =============================================================================

def make_seed_alu():
    """5 basic ALU instructions -- original seed."""
    prog = [
        encode_i(0x13, 0, 1, 0,  0),   # addi x1, x0, 0
        encode_i(0x13, 0, 2, 1,  1),   # addi x2, x1, 1
        encode_i(0x13, 0, 3, 1,  2),   # addi x3, x1, 2
        encode_r(0x33, 0, 0x00, 4, 1, 2),  # add  x4, x1, x2
        EBREAK,
    ]
    return program_to_bytes(prog)

def make_seed_branch():
    """Set up two registers, compare them, branch over an instruction."""
    prog = [
        encode_i(0x13, 0, 1, 0,  5),       # addi x1, x0, 5
        encode_i(0x13, 0, 2, 0, 10),       # addi x2, x0, 10
        encode_i(0x13, 0, 3, 0,  0),       # addi x3, x0, 0   (will be skipped or not)
        encode_b(0, 1, 2, 8),              # beq  x1, x2, +8  (x1!=x2, not taken)
        encode_r(0x33, 0, 0x00, 3, 1, 2), # add  x3, x1, x2
        encode_i(0x13, 4, 4, 3, 0xFF),    # xori x4, x3, 0xFF
        EBREAK,
    ]
    return program_to_bytes(prog)

def make_seed_memory():
    """Store a value to BRAM data area, then load it back."""
    prog = [
        encode_i(0x13, 0, 1, 0, 42),           # addi x1, x0, 42
        encode_s(2, 0, 1, DATA_BASE),           # sw   x1, DATA_BASE(x0)
        encode_i(0x03, 2, 2, 0, DATA_BASE),     # lw   x2, DATA_BASE(x0)
        encode_r(0x33, 0x4, 0x00, 3, 1, 2),    # xor  x3, x1, x2   (should be 0)
        EBREAK,
    ]
    return program_to_bytes(prog)

def make_seed_multiply():
    """Load two values and multiply them."""
    prog = [
        encode_i(0x13, 0, 1, 0,  7),           # addi x1, x0, 7
        encode_i(0x13, 0, 2, 0,  6),           # addi x2, x0, 6
        encode_r(0x33, 0, 0x01, 3, 1, 2),      # mul  x3, x1, x2   (=42)
        encode_r(0x33, 4, 0x00, 4, 3, 1),      # xor  x4, x3, x1
        encode_i(0x13, 7, 5, 3, 0x02A),        # andi x5, x3, 0x2A (mask low bits)
        EBREAK,
    ]
    return program_to_bytes(prog)

def setup_dirs():
    os.makedirs(CRASH_DIR, exist_ok=True)
    os.makedirs(SEED_DIR,  exist_ok=True)

    seeds = {
        "seed_alu.bin"      : make_seed_alu(),
        "seed_branch.bin"   : make_seed_branch(),
        "seed_memory.bin"   : make_seed_memory(),
        "seed_multiply.bin" : make_seed_multiply(),
    }

    for name, data in seeds.items():
        path = os.path.join(SEED_DIR, name)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
            print(f"  Created seed: {name} ({len(data)//4} instructions)")


# =============================================================================
# Simulation Runner
# =============================================================================

class Seed:
    def __init__(self, filename, content):
        self.filename = filename
        self.content  = bytes(content)
        self.fitness  = 0  # Toggle count

def load_seeds():
    seeds = []
    for fname in os.listdir(SEED_DIR):
        if fname.endswith(".bin"):
            with open(os.path.join(SEED_DIR, fname), "rb") as f:
                seeds.append(Seed(fname, f.read()))
    return seeds

def run_sim(executable, binary_path):
    try:
        result = subprocess.run(
            [executable, binary_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5
        )
        stdout = result.stdout
        start = stdout.find("{")
        end   = stdout.rfind("}")
        if start != -1 and end != -1:
            return json.loads(stdout[start:end+1])
    except Exception as e:
        print(f"  [sim error] {executable}: {e}")
    return None


# =============================================================================
# Main Fuzzing Loop
# =============================================================================

def main():
    print("=== NIRVFuzz ===")
    print("Setting up directories and seed corpus...")
    setup_dirs()

    population = load_seeds()
    if not population:
        print("No seeds found!")
        return

    # Count unique instruction lengths in initial population
    lengths = [len(bytes_to_program(s.content)) for s in population]
    print(f"Loaded {len(population)} seeds | lengths: {lengths}")

    global_coverage = set()
    pc_champions = {}  # Map PC -> seed filename
    
    print("Baselining initial coverage...")
    for s in population:
        # We need the PC coverage for the initial seeds.
        # Run them quickly through the clean sim.
        path = os.path.join(SEED_DIR, s.filename)
        res = run_sim(SIM_CLEAN, path)
        if res:
            s.fitness = res.get("toggle_count", 0)
            pcs = res.get("covered_pcs", [])
            global_coverage.update(pcs)
            for pc in pcs:
                # If this PC is new or the seed has better fitness than the current champion, update
                # Since initial seeds haven't been fully compared, we just assign them for now
                if pc not in pc_champions:
                    pc_champions[pc] = s.filename
                else:
                    # Find current champion fitness
                    curr_champ_name = pc_champions[pc]
                    curr_champ = next((seed for seed in population if seed.filename == curr_champ_name), None)
                    if curr_champ and s.fitness > curr_champ.fitness:
                        pc_champions[pc] = s.filename

    print(f"Initial global coverage: {len(global_coverage)} unique PCs")

    generation       = 0
    max_fitness_seen = max((s.fitness for s in population), default=0)
    total_crashes    = 0

    while True:
        generation += 1

        # ── Parent selection (roulette-wheel on toggle count fitness) ──────────
        total_fitness = sum(s.fitness for s in population)
        if total_fitness == 0:
            parent = random.choice(population)
        else:
            r, upto = random.uniform(0, total_fitness), 0
            parent = population[-1]
            for s in population:
                upto += s.fitness
                if upto >= r:
                    parent = s
                    break

        # ── Mutate ─────────────────────────────────────────────────────────────
        child_bytes = mutate(parent.content)
        child_path  = f"tmp_child_{os.getpid()}.bin"
        with open(child_path, "wb") as f:
            f.write(child_bytes)

        # ── Run clean simulation ───────────────────────────────────────────────
        clean_res = run_sim(SIM_CLEAN, child_path)
        if clean_res is None:
            if os.path.exists(child_path):
                os.remove(child_path)
            continue

        toggle_count = clean_res.get("toggle_count", 0)
        crc_clean    = clean_res.get("crc_out", 0)
        covered_pcs  = set(clean_res.get("covered_pcs", []))

        # ── Differential test: run buggy simulation ───────────────────────────
        buggy_res = run_sim(SIM_BUGGY, child_path)
        if buggy_res is not None:
            crc_buggy = buggy_res.get("crc_out", 0)
            if crc_clean != crc_buggy:
                total_crashes += 1
                prog_len = len(bytes_to_program(child_bytes))
                print(f"\n[!] DIVERGENCE #{total_crashes} at generation {generation}!")
                print(f"    Program length : {prog_len} instructions")
                print(f"    Clean CRC      : {hex(crc_clean)}")
                print(f"    Buggy CRC      : {hex(crc_buggy)}")
                crash_name = os.path.join(CRASH_DIR, f"crash_{generation}.bin")
                shutil.copy(child_path, crash_name)
                print(f"    Saved to       : {crash_name}")
                # Continue fuzzing -- accumulate all diverging inputs

        # ── Coverage-guided selection ──────────────────────────────────────────
        new_pcs = covered_pcs - global_coverage
        keep_seed = False
        is_champion = False

        if new_pcs:
            global_coverage.update(new_pcs)
            prog_len = len(bytes_to_program(child_bytes))
            print(f"\nGen {generation}: Discovered {len(new_pcs)} NEW PCs! "
                  f"(Total coverage: {len(global_coverage)} PCs, length {prog_len})")
            keep_seed = True
            is_champion = True

        elif toggle_count > max_fitness_seen:
            max_fitness_seen = toggle_count
            prog_len = len(bytes_to_program(child_bytes))
            print(f"\nGen {generation}: New max toggle = {toggle_count} "
                  f"({prog_len} instructions)")
            keep_seed = True

        # Check if it beats any existing champions for known PCs
        for pc in covered_pcs:
            if pc in pc_champions:
                curr_champ_name = pc_champions[pc]
                curr_champ = next((seed for seed in population if seed.filename == curr_champ_name), None)
                if curr_champ and toggle_count > curr_champ.fitness:
                    is_champion = True
                    keep_seed = True

        if keep_seed:
            new_seed = Seed(f"seed_gen{generation}.bin", child_bytes)
            new_seed.fitness = toggle_count
            population.append(new_seed)
            shutil.copy(child_path, os.path.join(SEED_DIR, new_seed.filename))

            # Update champions map
            for pc in covered_pcs:
                if pc not in pc_champions:
                    pc_champions[pc] = new_seed.filename
                else:
                    curr_champ_name = pc_champions[pc]
                    curr_champ = next((seed for seed in population if seed.filename == curr_champ_name), None)
                    if not curr_champ or toggle_count > curr_champ.fitness:
                        pc_champions[pc] = new_seed.filename

            # Cap population at 50, but protect champions (novelty-based culling)
            if len(population) > 50:
                population.sort(key=lambda s: s.fitness, reverse=True)
                
                # Find the lowest fitness seed that is NOT a champion for any PC
                favored_seeds = set(pc_champions.values())
                drop_target = None
                for i in range(len(population)-1, -1, -1):
                    if population[i].filename not in favored_seeds:
                        drop_target = i
                        break
                
                if drop_target is not None:
                    dropped = population.pop(drop_target)
                    try:
                        os.remove(os.path.join(SEED_DIR, dropped.filename))
                    except OSError:
                        pass
                else:
                    # All seeds are champions! Population dynamically expands to preserve novel paths.
                    pass

        # ── Cleanup ────────────────────────────────────────────────────────────
        if os.path.exists(child_path):
            os.remove(child_path)

        if generation % 100 == 0:
            sys.stdout.write(
                f"\rGen {generation:6d} | Pop {len(population):3d} | "
                f"MaxToggle {max_fitness_seen:6d} | Crashes {total_crashes:3d} ..."
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
