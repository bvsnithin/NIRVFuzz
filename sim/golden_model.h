#ifndef GOLDEN_MODEL_H
#define GOLDEN_MODEL_H

#include <vector>
#include <stdint.h>

class GoldenModel {
public:
    GoldenModel();
    
    // Load a raw binary into the simulated memory (starting at address 0)
    bool load_binary(const char* filename);

    // Step the simulator by one instruction. Returns false if EBREAK or TRAP.
    bool step();

    // Run until completion (EBREAK or max cycles)
    void run(int max_cycles);

    // Get the current running CRC-32 of the register state
    uint32_t get_crc() const;

    // Direct access to state for debugging
    uint32_t get_pc() const { return pc; }
    uint32_t get_reg(int i) const { return regs[i]; }

private:
    uint32_t pc;
    uint32_t regs[32];
    std::vector<uint8_t> memory;
    uint32_t crc_out;
    bool trapped;

    // CRC32 calculation table
    uint32_t crc_table[256];
    void init_crc_table();
    void update_crc(uint32_t reg_addr, uint32_t reg_data);
    
    // Instruction decoding and execution
    void execute_instruction(uint32_t inst);
};

#endif // GOLDEN_MODEL_H
