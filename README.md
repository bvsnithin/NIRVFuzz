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

The original plan was to implement this feedback loop on real hardware using a **Zybo Z7 FPGA board**. This is the same framework and it has been moved to **software simulation via Verilator** to overcome the limitations of hardware-software timing constraints. This gives full visibility into internal processor signals without the constraints of physical hardware.

---

## Proposed Framework

NIRVFuzz is built around three main pillars:

### 1. RTL Simulation with Verilator

The PicoRV32 RISC-V core is compiled into a fast C++ simulation model using Verilator. Two simulation binaries are produced:

- `Vtop` -- the clean, unmodified reference core.
- `Vtop_buggy` -- a core with an injected hardware fault (e.g., a modified ALU operation).

A C++ testbench (`sim_main.cpp`) loads a raw RISC-V binary into the simulated BRAM, releases the CPU from reset, runs the simulation to completion, and reports results as JSON.

### 2. Hardware Feedback Signals

Two feedback modules are instantiated alongside the CPU in `top.v` and tap directly onto the RVFI (RISC-V Formal Interface) outputs of PicoRV32:

- **CRC-32 Snooper (`crc32_snooper.v`)**: Computes a running CRC-32 checksum over every register write-back event during execution (register address + written value). The final CRC is a compact fingerprint of the register file's full execution history. It serves as the **differential oracle**: if the clean and buggy simulations produce different CRCs for the same input, a bug has been exposed.

- **Toggle Monitor (`toggle_monitor.v`)**: Counts the number of bit transitions (0-to-1 and 1-to-0) that occur each clock cycle across four internal buses: the retiring instruction's PC, the memory address bus, the register write-data bus, and the instruction word bus. The accumulated toggle count at the end of execution is the **coverage fitness score** used by the genetic algorithm. A high toggle count means the instruction sequence exercised a large number of distinct microarchitectural states.

### 3. Coverage-Guided Genetic Algorithm Fuzzer

The fuzzer (`fuzzer/fuzzer.py`) runs an evolutionary loop:

1. **Selection**: A parent seed is chosen from the current population using roulette-wheel selection weighted by toggle count fitness.
2. **Mutation**: The parent binary is mutated by randomly flipping bytes or individual bits, producing a child instruction sequence.
3. **Evaluation**: The child is run through both simulation binaries. The toggle count and CRC values are read from the JSON output.
4. **Differential Check**: If the clean CRC and buggy CRC diverge, the input is saved as a crash witness to `fuzzer/crashes/`.
5. **Population Update**: If the child achieves a new maximum toggle count, it is added to the population and saved to `seed_corpus/` for future mutation rounds.

A software-only **golden model** (`sim/golden_model.cpp`) independently computes the expected CRC by interpreting the RV32I instruction stream in software. This provides a fast reference for validation without requiring a full RTL simulation pass.

---

## Repository Structure

```
NIRVFuzz/
├── rtl/
│   ├── top.v               # Top-level RTL wrapper
│   ├── picorv32.v          # Clean PicoRV32 reference core
│   ├── buggy_picorv32.v    # Faulty core with injected bug
│   ├── bram.v              # 64KB dual-port BRAM
│   ├── crc32_snooper.v     # Register write-back CRC feedback module
│   └── toggle_monitor.v    # Bit-toggle coverage feedback module
├── sim/
│   ├── sim_main.cpp        # Verilator C++ testbench and simulation driver
│   ├── golden_model.cpp    # Software RV32I interpreter for expected CRC
│   └── golden_model.h
├── fuzzer/
│   ├── fuzzer.py           # Coverage-guided genetic algorithm fuzzer
│   └── crashes/            # Diverging inputs saved here
├── seed_corpus/
│   └── seed1.bin           # Initial RISC-V instruction seed
└── Makefile                # Builds Vtop and Vtop_buggy via Verilator
```

---

## Building and Running

**Requirements**: Verilator, Python 3, a C++ compiler.

```bash
# Build both simulation binaries
make

# Run the fuzzer
make fuzz

# Clean all build artifacts
make clean
```

When a divergence is found, the crashing input is saved to `fuzzer/crashes/`. To generate waveforms for manual inspection:

```bash
sim/Vtop       fuzzer/crashes/crash_N.bin trace_clean.vcd
sim/Vtop_buggy fuzzer/crashes/crash_N.bin trace_buggy.vcd
```

Open the resulting `.vcd` files in GTKWave or any compatible waveform viewer to compare the two execution traces side by side.
