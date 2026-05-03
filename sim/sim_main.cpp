#include <iostream>
#include <fstream>
#include <vector>
#include <set>
#include <iomanip>
#include <stdint.h>
#include "Vtop.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

// Golden model will be included here
#include "golden_model.h"

vluint64_t main_time = 0;
double sc_time_stamp() { return main_time; }

void tick(Vtop* top, VerilatedVcdC* tfp) {
    top->clk = 1;
    top->eval();
    if (tfp) tfp->dump(main_time);
    main_time += 5;

    top->clk = 0;
    top->eval();
    if (tfp) tfp->dump(main_time);
    main_time += 5;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <riscv_binary.bin> [vcd_file]" << std::endl;
        return 1;
    }

    const char* bin_file = argv[1];
    const char* vcd_file = (argc > 2) ? argv[2] : nullptr;

    // Load binary
    std::ifstream file(bin_file, std::ios::binary);
    if (!file) {
        std::cerr << "Error: Could not open " << bin_file << std::endl;
        return 1;
    }
    std::vector<uint8_t> memory(std::istreambuf_iterator<char>(file), {});

    const size_t BRAM_BYTES = 65536;
    if (memory.size() > BRAM_BYTES) {
        std::cerr << "Warning: binary (" << memory.size() << " B) exceeds BRAM ("
                  << BRAM_BYTES << " B); truncating." << std::endl;
        memory.resize(BRAM_BYTES);
    }

    Vtop* top = new Vtop;

    VerilatedVcdC* tfp = nullptr;
    if (vcd_file) {
        Verilated::traceEverOn(true);
        tfp = new VerilatedVcdC;
        top->trace(tfp, 99);
        tfp->open(vcd_file);
    }

    // Initialize inputs
    top->clk = 0;
    top->rst_n = 0;
    top->cpu_resetn = 0;
    top->mem_a_en = 0;
    top->mem_a_we = 0;
    top->mem_a_be = 0;
    top->mem_a_addr = 0;
    top->mem_a_din = 0;

    // Reset sequence
    for (int i = 0; i < 5; i++) {
        tick(top, tfp);
    }
    top->rst_n = 1;
    for (int i = 0; i < 5; i++) {
        tick(top, tfp);
    }

    // Load binary into BRAM via Port A
    top->mem_a_en = 1;
    top->mem_a_we = 1;
    top->mem_a_be = 0xF;
    
    for (size_t i = 0; i < memory.size(); i += 4) {
        uint32_t word = 0;
        for (int j = 0; j < 4; j++) {
            if (i + j < memory.size()) {
                word |= (memory[i + j] << (8 * j));
            }
        }
        top->mem_a_addr = (i / 4);
        top->mem_a_din = word;
        tick(top, tfp);
    }

    // Finish loading BRAM
    top->mem_a_en = 0;
    top->mem_a_we = 0;
    top->mem_a_din = 0;
    tick(top, tfp);

    // Release CPU reset
    top->cpu_resetn = 1;

    // Run Simulation
    int max_cycles = 10000;
    int cycles = 0;
    
    // Setup and run Golden Model
    GoldenModel gm;
    gm.load_binary(bin_file);
    gm.run(max_cycles);
    uint32_t expected_crc = gm.get_crc();

    std::set<uint32_t> covered_pcs;

    while (!Verilated::gotFinish() && cycles < max_cycles) {
        tick(top, tfp);
        cycles++;
        
        if (top->rvfi_valid_out) {
            covered_pcs.insert(top->pc_out);
        }

        if (top->done) break;
        if (top->trap) break;
    }
    
    // One extra tick to latch the final RVFI signals
    tick(top, tfp);

    // Golden model validation: warn if clean RTL disagrees with reference model
    if (!top->trap && expected_crc != top->crc_out) {
        std::cerr << "GOLDEN_MISMATCH: golden=" << expected_crc
                  << " rtl=" << top->crc_out << std::endl;
    }

    // Output JSON-like format for the fuzzer to parse easily
    std::cout << "{" << std::endl;
    std::cout << "  \"cycles\": " << cycles << "," << std::endl;
    std::cout << "  \"trap\": " << (int)top->trap << "," << std::endl;
    std::cout << "  \"done\": " << (int)top->done << "," << std::endl;
    std::cout << "  \"pc\": " << top->pc_out << "," << std::endl;
    std::cout << "  \"insn\": " << top->insn_out << "," << std::endl;
    std::cout << "  \"crc_out\": " << top->crc_out << "," << std::endl;
    std::cout << "  \"golden_crc\": " << expected_crc << "," << std::endl;
    std::cout << "  \"toggle_count\": " << top->toggle_count_out << "," << std::endl;
    
    std::cout << "  \"covered_pcs\": [";
    bool first_pc = true;
    for (uint32_t pc : covered_pcs) {
        if (!first_pc) std::cout << ", ";
        std::cout << pc;
        first_pc = false;
    }
    std::cout << "]" << std::endl;
    
    std::cout << "}" << std::endl;

    if (tfp) {
        tfp->close();
        delete tfp;
    }
    delete top;

    return 0;
}
