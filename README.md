# NIRVFuzz

**N**etwork **I**nstruction **R**andomization with **V**erilator-based **Fuzz** testing for RISC-V processors.

---

## What Is This Project?

NIRVFuzz is a simulation-based, coverage-guided fuzzing framework for RISC-V processor cores. It automatically generates and mutates sequences of RISC-V machine instructions, runs them through a Verilator simulation of the target CPU, and uses hardware-observable feedback signals to guide the search toward inputs that expose bugs.

The framework targets the **PicoRV32** RISC-V core and implements **differential testing**: the same instruction stream is executed on both a clean reference model and an intentionally modified (buggy) version of the core. Any divergence in the observed register-state fingerprint is flagged as a potential hardware bug.

---

## Motivation

This project is inspired by and built as an improvement on the **RISCover** paper, which proposed a methodology for testing RISC-V processor implementations using randomly generated instruction sequences.

A key limitation of RISCover is that it operates **without feedback**. Inputs are generated or selected with no knowledge of what the previous test cases actually exercised inside the processor. This means:

- Many generated inputs redundantly cover the same execution paths.
- Rare or complex microarchitectural states are unlikely to be reached.
- The search for bug-triggering inputs is essentially blind.

NIRVFuzz addresses this gap by introducing a **feedback loop** directly from the RTL simulation into the fuzzer. Instead of treating the processor as a black box, the fuzzer observes internal hardware signals every clock cycle and uses those observations to decide which test cases are worth mutating further. This turns the testing process from random exploration into a directed, coverage-guided search.

The original plan was to implement this feedback loop on real hardware using a **Zybo Z7 FPGA board**. The framework has since been moved to **software simulation via Verilator** to overcome hardware-software timing constraints. This gives full visibility into internal processor signals without the constraints of physical hardware.

---

## Relation to RISCover and Our Innovation

### What RISCover Does

RISCover (Thomas et al., CCS 2025) is a user-space differential fuzzing framework for discovering architectural security vulnerabilities in **closed-source, manufactured RISC-V silicon**. Its key properties are:

- **Black-box by design.** RISCover requires no source code, no RTL, and no hardware modifications. It runs entirely in user space on Linux or Android, making it applicable to commercial off-the-shelf RISC-V chips.
- **Differential oracle via majority vote.** It executes the same instruction sequence across several physical RISC-V CPUs simultaneously. Any deviation from the majority output is flagged as a potential bug. This eliminates the need for a reference model — correct behavior is inferred from the consensus of multiple implementations.
- **Frequency-weighted random generation.** Instruction sequences are generated randomly, with each instruction chosen inversely proportional to its frequency in 1.36 billion real-world instructions disassembled from Debian packages. This biases generation toward rare and undocumented instructions that are more likely to be handled incorrectly.
- **Short sequences only.** RISCover's optimal sequence length is 3–5 instructions. Longer sequences cause network congestion across clients and provide only marginal additional coverage. There is no mechanism to build upon sequences that previously revealed interesting behavior.
- **No feedback, no learning.** Every iteration generates a new sequence from scratch. There is no memory of what prior iterations exercised, no concept of a seed population, and no selection pressure toward unexplored states. Each test is independent of all others.

RISCover is highly effective in its target setting and discovered four previously unknown vulnerabilities, including GhostWrite — an unprivileged physical memory write primitive in the T-Head C910/C920 that enables privilege escalation to machine-mode execution.

---

### The Fundamental Gap RISCover Leaves Open

RISCover's black-box constraint is what makes it deployable on real silicon, but it comes at a direct cost to search efficiency. Without any feedback from the processor's internal state, the fuzzer cannot distinguish a test case that exercised ten new microarchitectural states from one that replicated the exact same execution path as a thousand prior runs. The search is structurally blind.

This gap is well-understood in software security. Pure random fuzzing was the dominant approach until AFL (Zalewski, 2013) introduced **coverage-guided mutation**: instead of generating inputs from scratch, AFL mutates a corpus of previously interesting inputs, guided by whether each mutation reaches new code paths. The improvement in bug-finding efficiency was dramatic — AFL and its descendants (libFuzzer, honggfuzz) became the de-facto standard for software vulnerability research precisely because feedback transforms a random walk into a directed search.

The same principle has never been applied to RISCover's setting with hardware-observable signals, because RISCover's black-box constraint makes internal processor state inaccessible. **NIRVFuzz applies the coverage-guided paradigm to the pre-silicon RTL verification phase**, where full internal visibility is available through simulation.

---

### NIRVFuzz's Specific Innovations Over RISCover

#### 1. Hardware-Observable Coverage Feedback

NIRVFuzz introduces two hardware feedback modules that tap directly onto the RTL through the RISC-V Formal Interface (RVFI):

- **Toggle Monitor**: Counts bit-level transitions (0→1, 1→0) on four internal buses per clock cycle — the retiring PC, memory address bus, register write-data, and instruction word. The accumulated toggle count is a proxy for the number of distinct microarchitectural states exercised. RISCover has no equivalent signal.
- **PC Coverage**: Tracks the set of program counter addresses that retire during execution. Each new PC reached represents a previously unexplored code path in the processor's RTL.

These signals give the fuzzer information RISCover fundamentally cannot access: *was this instruction sequence more or less interesting than the last one?*

#### 2. Evolutionary Corpus and Fitness-Guided Selection

RISCover discards every test case after evaluation. NIRVFuzz maintains a **population of up to 50 seeds**, each scored by its toggle count. Parent selection uses roulette-wheel weighting so that sequences that previously exercised many distinct states are proportionally more likely to be mutated further. Sequences that discover new PC addresses are preserved regardless of toggle count, to protect path diversity.

This is the AFL model applied to hardware: instead of generating fresh random sequences each iteration, the fuzzer invests more effort in regions of the instruction space that have already shown promise.

#### 3. Instruction-Level Mutation

RISCover generates each sequence from scratch. NIRVFuzz mutates existing programs using four instruction-level operations that preserve program validity:

| Operation | Probability (normal / plateau) | Effect |
|---|---|---|
| Replace | 40% / 45% | Swap one instruction for a new valid one |
| Insert  | 30% / 40% | Add a new instruction at a random position |
| Delete  | 20% / 10% | Remove one instruction |
| Swap    | 10% / 5%  | Exchange two instructions |

All mutations preserve the RV32IM encoding invariants (correct opcode fields, aligned branch targets, safe memory addresses), so every generated child is a syntactically valid program that will actually execute rather than immediately trap.

#### 4. Adaptive Mutation on Coverage Plateau

When global PC coverage stops growing for more than `PLATEAU_THRESHOLD` (default 500) generations, NIRVFuzz automatically increases the insert and replace probabilities to push the search into structurally different regions of the instruction space. RISCover has no equivalent adaptation mechanism — its generation weights are fixed and derived from a static corpus.

#### 5. Richer Differential Oracle

RISCover's oracle requires multiple physical CPUs and uses majority vote to infer ground truth. NIRVFuzz uses three independent checks:

- **CRC divergence**: The CRC-32 of the register write-back history must match between the clean and buggy RTL variants. Any difference means the bug was exercised.
- **Trap divergence**: If one variant traps and the other does not, that is flagged as a separate class of divergence — a bug class that a CRC-only oracle would miss entirely.
- **Golden model mismatch**: A software RV32IM interpreter computes the expected CRC independently of both RTL variants. If the *clean* RTL disagrees with the golden model, it indicates a clean-RTL correctness regression — a class of bug that differential testing between two RTL variants would never detect.

#### 6. Automatic Crash Minimization

RISCover's sequences are already short (3–5 instructions), so minimization is not needed. NIRVFuzz supports programs up to 32 instructions, and longer programs are harder to analyze manually. After finding a diverging program, NIRVFuzz automatically runs a greedy delta-debugger that removes instructions one at a time until the smallest still-diverging program is found. This is saved alongside the full crash, making root-cause analysis significantly faster.

---

### How NIRVFuzz and RISCover Relate in the Development Lifecycle

The two frameworks are not competing — they target **different phases of the processor development lifecycle**:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Design Phase          │  Tape-out          │  Post-Silicon         │
│  (RTL available)       │                    │  (manufactured chip)  │
├─────────────────────────────────────────────────────────────────────┤
│  ◄── NIRVFuzz ────────►│                    │  ◄── RISCover ───────►│
│  Gray-box, simulation  │                    │  Black-box, real HW   │
│  Full internal access  │                    │  No source required   │
│  Coverage-guided       │                    │  Majority-vote oracle │
└─────────────────────────────────────────────────────────────────────┘
```

NIRVFuzz's position: catch bugs **earlier**, when the RTL is still modifiable and bugs are cheapest to fix, by making the search smarter than random. RISCover's position: catch bugs that **survived** the design phase and reached manufactured silicon, operating without any source code access.

The academic contribution of NIRVFuzz is to answer the question: *if you do have access to the RTL, how much better can you do than RISCover's random search?* Coverage-guided selection, evolutionary mutation, and hardware-observable feedback are the answer.

---

### Summary Comparison

| Property | RISCover | NIRVFuzz |
|---|---|---|
| Target | Manufactured silicon | RTL simulation |
| Source code required | No | Yes |
| Feedback signal | None | Toggle count + PC coverage |
| Test case selection | Independent random per iteration | Fitness-weighted roulette over corpus |
| Mutation | Generate from scratch | Instruction-level mutate (replace/insert/delete/swap) |
| Sequence length | 3–5 instructions (optimal) | Up to 32 instructions |
| Adaptation | Fixed generation weights | Adaptive mutation on coverage plateau |
| Oracle | Majority vote across physical CPUs | CRC diff + trap diff + golden model |
| Crash minimization | Not needed (sequences already short) | Automatic delta-debugging |
| Phase | Post-silicon security testing | Pre-silicon design verification |

---

## Architecture Overview

NIRVFuzz is built around three main pillars:

### 1. RTL Simulation with Verilator

The PicoRV32 RISC-V core is compiled into a fast C++ simulation model using Verilator. Two simulation binaries are produced:

- `sim/Vtop` — the clean, unmodified reference core.
- `sim/Vtop_buggy` — a core with an injected hardware fault (e.g., a modified ALU operation).

A C++ testbench (`sim/sim_main.cpp`) loads a raw RISC-V binary into the simulated BRAM, releases the CPU from reset, runs the simulation to completion, and reports results as a JSON object on stdout. The two binaries are run **concurrently** by the fuzzer to halve per-iteration wall time.

### 2. Hardware Feedback Signals

Two feedback modules are instantiated alongside the CPU in `rtl/top.v` and tap directly onto the RVFI (RISC-V Formal Interface) outputs of PicoRV32:

- **CRC-32 Snooper (`rtl/crc32_snooper.v`)**: Computes a running CRC-32 checksum over every register write-back event during execution (register address + written value). The final CRC is a compact fingerprint of the register file's complete execution history. It is the **differential oracle**: if the clean and buggy simulations produce different CRCs for the same input, a bug has been exposed.

- **Toggle Monitor (`rtl/toggle_monitor.v`)**: Counts bit transitions (0→1 and 1→0) that occur each clock cycle across four internal buses: the retiring instruction's PC, the memory address bus, the register write-data bus, and the instruction word bus. The accumulated toggle count at the end of execution is the **coverage fitness score** used by the genetic algorithm. A high toggle count means the instruction sequence exercised many distinct microarchitectural states.

### 3. Coverage-Guided Genetic Algorithm Fuzzer

The fuzzer (`fuzzer/fuzzer.py`) runs an evolutionary loop:

1. **Selection**: A parent seed is chosen from the population using roulette-wheel selection weighted by toggle-count fitness.
2. **Mutation**: The parent is mutated by one of four instruction-level operations (probabilities adapt when coverage plateaus):
   - **Replace** (40% / 45% aggressive): replace a random instruction with a new valid one.
   - **Insert** (30% / 40% aggressive): insert a new instruction at a random position.
   - **Delete** (20% / 10% aggressive): remove a random instruction.
   - **Swap** (10% / 5% aggressive): swap two instructions.
3. **Parallel Evaluation**: The child is run through both simulation binaries concurrently. Toggle count, CRC, trap status, covered PCs, and the golden model CRC are read from the JSON output.
4. **Differential Check**: Three divergence conditions are flagged as bugs:
   - CRC mismatch between clean and buggy RTL.
   - Trap-status mismatch between clean and buggy RTL.
   - CRC mismatch between clean RTL and the software golden model.
5. **Crash Minimization**: Each crashing input is immediately delta-debugged — instructions are removed one at a time until the smallest still-diverging program is found. Both the full crash and the minimized version are saved.
6. **Population Update**: The child is kept if it discovers new PC addresses, achieves a new maximum toggle count, or beats the current fitness champion for any covered PC. The population is capped at 50 seeds using novelty-preserving culling.
7. **Adaptive Mutation**: After `PLATEAU_THRESHOLD` (default 500) generations without new PC coverage, the fuzzer automatically switches to aggressive mutation rates to push out of local optima.

A software **golden model** (`sim/golden_model.cpp`) independently interprets the RV32IM instruction stream and computes the expected CRC. This provides a fast cross-check that the clean RTL is itself behaving correctly, not just that it differs from the buggy variant.

---

## Repository Structure

```
NIRVFuzz/
├── Makefile                    # Builds Vtop and Vtop_buggy via Verilator
├── setup.sh                    # One-time environment setup
├── rtl/
│   ├── top.v                   # Top-level RTL wrapper; instantiates CPU, BRAM, and feedback modules
│   ├── picorv32.v              # Clean PicoRV32 reference core (unmodified)
│   ├── buggy_picorv32.v        # PicoRV32 with an injected hardware bug
│   ├── bram.v                  # 64KB dual-port synchronous BRAM
│   ├── crc32_snooper.v         # CRC-32 over register write-back events (differential oracle)
│   └── toggle_monitor.v        # Bit-toggle accumulator (coverage fitness signal)
├── sim/
│   ├── sim_main.cpp            # Verilator testbench: loads binary, runs simulation, outputs JSON
│   ├── golden_model.cpp        # Software RV32IM interpreter; computes expected CRC independently
│   ├── golden_model.h          # Golden model header
│   ├── Vtop                    # Compiled clean simulation binary (generated by make)
│   └── Vtop_buggy              # Compiled buggy simulation binary (generated by make)
├── fuzzer/
│   ├── fuzzer.py               # Main fuzzer: genetic algorithm, mutation, differential testing
│   ├── run_regression.py       # Regression runner: verifies saved crashes still reproduce
│   └── crashes/                # Diverging inputs saved here (crash_N.bin + crash_N_min.bin)
├── seed_corpus/
│   ├── seed_alu.bin            # Seed: basic ALU operations
│   ├── seed_branch.bin         # Seed: branch and compare sequence
│   ├── seed_memory.bin         # Seed: store-then-load round trip
│   ├── seed_multiply.bin       # Seed: RV32M multiply operations
│   └── seed_gen*.bin           # Auto-generated seeds saved during fuzzing runs
└── build/
    ├── clean/                  # Verilator intermediate files for Vtop
    └── buggy/                  # Verilator intermediate files for Vtop_buggy
```

---

## Requirements

- **Verilator** (tested with 4.x / 5.x)
- **Python 3.8+** (uses `random.choices`, `concurrent.futures`)
- **GCC / Clang** (C++14 or later)
- **GTKWave** (optional, for waveform inspection)

---

## Quick Start

```bash
# 1. Build both simulation binaries (only needed once, or after RTL/C++ changes)
make

# 2. Run the fuzzer
make fuzz

# 3. Clean all build artifacts and generated seeds
#    (hand-crafted seeds in seed_corpus/ are preserved)
make clean
```

You can also run the fuzzer directly:

```bash
cd fuzzer
python3 fuzzer.py
```

---

## Detailed Usage

### Building

```bash
make          # builds sim/Vtop (clean) and sim/Vtop_buggy (buggy)
make clean    # removes build/, sim/Vtop, sim/Vtop_buggy, fuzzer/crashes/, and seed_gen*.bin
```

The `Makefile` invokes Verilator with `-DRISCV_FORMAL` to enable the RVFI interface on PicoRV32, compiles `sim/sim_main.cpp` and `sim/golden_model.cpp` into both binaries, and places the results under `build/clean/` and `build/buggy/`.

### Running the Fuzzer

```bash
make fuzz
# equivalent to:
cd fuzzer && python3 fuzzer.py
```

The fuzzer prints a progress line every 100 generations:

```
Gen   1200 | Pop  48 | MaxToggle   3841 | Crashes   3 | GoldenMM  0 | 87s ...
```

Key events print inline:

```
Gen 312: Discovered 4 NEW PCs! (Total coverage: 91 PCs, length 7)

[!] DIVERGENCE #1 at generation 419!
    Program length : 9 instructions
    Reason         : CRC  clean=0x4a3f1c28 buggy=0x91be4401
    Saved to       : crashes/crash_419.bin
    Minimizing... 9 → 3 instructions → crashes/crash_419_min.bin

[plateau] No new PCs for 500 gens (coverage=104, pop=47, mutation=normal)
[plateau] No new PCs for 1000 gens (coverage=104, pop=47, mutation=aggressive)
```

### Configuring the Fuzzer

Edit the constants near the top of `fuzzer/fuzzer.py`:

| Constant | Default | Effect |
|---|---|---|
| `MAX_GENERATIONS` | `0` | Stop after N generations (0 = run forever) |
| `MAX_RUNTIME_SECS` | `0` | Stop after N seconds (0 = run forever) |
| `PLATEAU_THRESHOLD` | `500` | Gens without new PC coverage before switching to aggressive mutation |
| `PLATEAU_REPORT_INTERVAL` | `500` | How often to print the plateau warning while stalled |
| `MAX_PROG_LEN` | `32` | Maximum instructions per generated program |

### Inspecting Crashes

Every detected divergence produces two files in `fuzzer/crashes/`:

| File | Contents |
|---|---|
| `crash_N.bin` | Full crashing program as found |
| `crash_N_min.bin` | Minimized version (fewest instructions that still diverge) |
| `golden_mismatch_N.bin` | Program where clean RTL disagrees with golden model |

To generate waveforms for side-by-side comparison:

```bash
# Run both sims with VCD output
sim/Vtop       fuzzer/crashes/crash_N_min.bin trace_clean.vcd
sim/Vtop_buggy fuzzer/crashes/crash_N_min.bin trace_buggy.vcd

# Open in GTKWave
gtkwave trace_clean.vcd &
gtkwave trace_buggy.vcd &
```

To decode a crash binary back to readable assembly instructions, use the `objdump` tool provided by your RISC-V toolchain (e.g. `riscv-none-elf-objdump` or `riscv64-unknown-elf-objdump`):

```bash
riscv-none-elf-objdump -b binary -m riscv:rv32 -D fuzzer/crashes/crash_N_min.bin
```

This will output the exact assembly sequence. You can inspect it to see which specific instruction the buggy CPU evaluated incorrectly!

### Running Regression Tests

After modifying the RTL and rebuilding, verify that all previously found crashes still reproduce:

```bash
cd fuzzer
python3 run_regression.py
```

Output:

```
=== NIRVFuzz Regression — 5 crashes ===

  [PASS] crash_419.bin — CRC  clean=0x4a3f1c28 buggy=0x91be4401
  [PASS] crash_731.bin — Trap clean=0 buggy=1
  [FAIL] crash_512.bin — no longer diverges!
  ...

Results: 4 pass, 1 fail, 0 skip (out of 5 crashes)
```

Exit code is `0` if all crashes still reproduce, `1` if any do not.

---

## How Each File Works

### `rtl/top.v`
Top-level Verilog wrapper. Instantiates PicoRV32, the dual-port BRAM, the CRC snooper, and the toggle monitor. Wires Port A of the BRAM to the testbench (for loading programs) and Port B to the CPU (for instruction fetch and data access). Drives `done` high when EBREAK retires or the CPU traps, which latches the final CRC and toggle count.

### `rtl/picorv32.v` / `rtl/buggy_picorv32.v`
The PicoRV32 RISC-V core. `picorv32.v` is the clean reference. `buggy_picorv32.v` is an intentionally modified copy with a fault injected (the fuzzer's goal is to find inputs that expose this fault). Only one is linked per simulation binary.

### `rtl/bram.v`
A 64KB synchronous dual-port BRAM. Port A is used by the testbench to load programs before releasing the CPU from reset. Port B is the live CPU data/instruction port.

### `rtl/crc32_snooper.v`
Watches RVFI register write-back events. For every retiring instruction that writes a non-zero register, it feeds `{reg_addr, reg_data}` (40 bits) into a CRC-32 (IEEE 802.3 reflected polynomial) accumulator. The final CRC is latched when `done_i` goes high. This CRC is the **primary differential oracle**.

### `rtl/toggle_monitor.v`
Each clock cycle, XORs the current values of four buses against their previous-cycle values and counts the set bits (bit-flips). The running total is accumulated in a 32-bit register and latched on `done_i`. This **toggle count** is the fitness signal for the genetic algorithm: higher means more distinct hardware states were exercised.

### `sim/sim_main.cpp`
Verilator C++ testbench. Loads the binary into BRAM via Port A, runs the golden model in parallel, releases the CPU, clocks the simulation until `done` or `trap` or 10,000 cycles, then prints a JSON object with: `cycles`, `trap`, `done`, `crc_out`, `golden_crc`, `toggle_count`, and `covered_pcs`. Also warns on stderr if the binary exceeds BRAM size or if the clean RTL's CRC disagrees with the golden model.

### `sim/golden_model.cpp`
A pure software RV32IM interpreter. Loads the same binary, executes it instruction-by-instruction, and applies the same CRC-32 update logic as `crc32_snooper.v` on every register write. Produces an expected CRC that the fuzzer compares against the clean RTL's output to detect clean-RTL correctness regressions independently of the buggy RTL.

### `fuzzer/fuzzer.py`
The main fuzzer. Implements the full genetic algorithm loop: seed loading, fitness baselining, roulette-wheel parent selection, instruction-level mutation (replace/insert/delete/swap), parallel simulation, differential divergence detection, coverage-guided population management, adaptive mutation on plateau, crash minimization, and golden model mismatch detection.

### `fuzzer/run_regression.py`
Standalone regression runner. Loads every `crash_*.bin` from `fuzzer/crashes/`, re-runs both simulations concurrently, and checks whether each still triggers divergence. Useful after RTL changes to confirm bug fixes without re-running the full fuzzer.

---

## Output Files

| Path | Created by | Description |
|---|---|---|
| `sim/Vtop` | `make` | Clean simulation binary |
| `sim/Vtop_buggy` | `make` | Buggy simulation binary |
| `seed_corpus/seed_gen*.bin` | fuzzer | Seeds discovered during fuzzing that improved coverage |
| `fuzzer/crashes/crash_N.bin` | fuzzer | Raw crashing program found at generation N |
| `fuzzer/crashes/crash_N_min.bin` | fuzzer | Delta-debugged minimal crash |
| `fuzzer/crashes/golden_mismatch_N.bin` | fuzzer | Program where clean RTL diverges from golden model |
| `build/` | `make` | Verilator intermediate C++ sources and object files |

---

## Extending NIRVFuzz

**Testing a different buggy core**: Replace `rtl/buggy_picorv32.v` and run `make` to rebuild.

**Adding new seed programs**: Write a `.bin` file using the instruction encoders in `fuzzer.py` or any RISC-V assembler, place it in `seed_corpus/`, and restart the fuzzer. Existing generated seeds (`seed_gen*.bin`) are not affected.

**Changing mutation rates**: Edit `p_replace`, `p_insert`, `p_delete` thresholds in `mutate()` inside `fuzzer/fuzzer.py`.

**Running for a fixed time**: Set `MAX_RUNTIME_SECS = 3600` (for example) near the top of `fuzzer/fuzzer.py`.
