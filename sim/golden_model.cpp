#include "golden_model.h"
#include <iostream>
#include <fstream>
#include <cstring>

GoldenModel::GoldenModel() {
    pc = 0;
    std::memset(regs, 0, sizeof(regs));
    crc_out = 0xFFFFFFFF;
    trapped = false;
    memory.resize(65536, 0); // 64KB memory

    init_crc_table();
}

void GoldenModel::init_crc_table() {
    // Reflected CRC-32 (IEEE 802.3) — matches crc32_snooper.v which uses 0xEDB88320
    uint32_t polynomial = 0xEDB88320;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int j = 0; j < 8; j++) {
            if (c & 1) c = polynomial ^ (c >> 1); // LSB-first (reflected)
            else       c = c >> 1;
        }
        crc_table[i] = c;
    }
}

void GoldenModel::update_crc(uint32_t reg_addr, uint32_t reg_data) {
    if (reg_addr == 0) return; // x0 doesn't update CRC in snooper

    // Mirror crc32_snooper.v exactly:
    //   40-bit input: {3'b000, wr_addr[4:0], wr_data[31:0]}
    //   Processed as 5 bytes, LSB-first:
    //     byte0 = wr_data[7:0], byte1 = wr_data[15:8],
    //     byte2 = wr_data[23:16], byte3 = wr_data[31:24],
    //     byte4 = {000, wr_addr[4:0]}
    uint8_t bytes[5];
    bytes[0] = (reg_data >>  0) & 0xFF;
    bytes[1] = (reg_data >>  8) & 0xFF;
    bytes[2] = (reg_data >> 16) & 0xFF;
    bytes[3] = (reg_data >> 24) & 0xFF;
    bytes[4] = reg_addr & 0x1F;   // 5-bit register address

    for (int i = 0; i < 5; i++) {
        uint8_t d = bytes[i];
        // Reflected CRC update: table indexed by (crc XOR byte) low byte, shift right
        crc_out = crc_table[(crc_out ^ d) & 0xFF] ^ (crc_out >> 8);
    }
}

bool GoldenModel::load_binary(const char* filename) {
    std::ifstream file(filename, std::ios::binary);
    if (!file) return false;
    
    file.read(reinterpret_cast<char*>(memory.data()), memory.size());
    return true;
}

void GoldenModel::run(int max_cycles) {
    for (int i = 0; i < max_cycles; i++) {
        if (!step()) break;
    }
}

uint32_t GoldenModel::get_crc() const {
    return ~crc_out; // Final CRC usually inverted
}

bool GoldenModel::step() {
    if (trapped || pc >= memory.size()) return false;

    uint32_t inst = 0;
    // Little endian fetch
    inst |= memory[pc];
    inst |= (memory[pc + 1] << 8);
    inst |= (memory[pc + 2] << 16);
    inst |= (memory[pc + 3] << 24);

    if (inst == 0x00100073) { // EBREAK
        return false;
    }

    execute_instruction(inst);
    regs[0] = 0; // x0 is hardwired to 0
    return !trapped;
}

void GoldenModel::execute_instruction(uint32_t inst) {
    uint32_t opcode = inst & 0x7F;
    uint32_t rd = (inst >> 7) & 0x1F;
    uint32_t rs1 = (inst >> 15) & 0x1F;
    uint32_t rs2 = (inst >> 20) & 0x1F;
    uint32_t funct3 = (inst >> 12) & 0x7;
    uint32_t funct7 = (inst >> 25) & 0x7F;
    
    uint32_t next_pc = pc + 4;
    bool write_reg = false;
    uint32_t rd_val = 0;

    // Decoding logic for basic RV32I
    switch (opcode) {
        case 0x33: { // R-type: RV32I ALU + RV32M multiply/divide
            uint32_t val1 = regs[rs1];
            uint32_t val2 = regs[rs2];
            write_reg = true;
            if (funct7 == 0x01) { // RV32M extension
                int32_t s1 = (int32_t)val1, s2 = (int32_t)val2;
                switch (funct3) {
                    case 0: rd_val = (uint32_t)(s1 * s2); break;                         // MUL
                    case 1: rd_val = (uint32_t)(((int64_t)s1 * s2) >> 32); break;        // MULH
                    case 2: rd_val = (uint32_t)(((int64_t)s1*(uint64_t)val2)>>32); break; // MULHSU
                    case 3: rd_val = (uint32_t)(((uint64_t)val1*val2)>>32); break;       // MULHU
                    case 4: rd_val = (s2==0) ? 0xFFFFFFFF : (uint32_t)(s1/s2); break;   // DIV
                    case 5: rd_val = (val2==0) ? 0xFFFFFFFF : (val1/val2); break;        // DIVU
                    case 6: rd_val = (s2==0) ? (uint32_t)s1 : (uint32_t)(s1%s2); break; // REM
                    case 7: rd_val = (val2==0) ? val1 : (val1%val2); break;              // REMU
                    default: write_reg = false; break;
                }
            } else { // RV32I standard ALU
                if      (funct3 == 0) rd_val = (funct7 == 0x20) ? (val1 - val2) : (val1 + val2);
                else if (funct3 == 1) rd_val = val1 << (val2 & 0x1F);
                else if (funct3 == 2) rd_val = ((int32_t)val1 < (int32_t)val2) ? 1 : 0;
                else if (funct3 == 3) rd_val = (val1 < val2) ? 1 : 0;
                else if (funct3 == 4) rd_val = val1 ^ val2;
                else if (funct3 == 5) rd_val = (funct7==0x20) ? ((int32_t)val1>>(val2&0x1F)) : (val1>>(val2&0x1F));
                else if (funct3 == 6) rd_val = val1 | val2;
                else if (funct3 == 7) rd_val = val1 & val2;
                else write_reg = false;
            }
            break;
        }
        case 0x13: { // I-type (ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SLLI, SRLI, SRAI)
            uint32_t val1 = regs[rs1];
            int32_t imm = ((int32_t)inst) >> 20;
            write_reg = true;
            if (funct3 == 0) rd_val = val1 + imm;
            else if (funct3 == 2) rd_val = ((int32_t)val1 < imm) ? 1 : 0;
            else if (funct3 == 3) rd_val = (val1 < (uint32_t)imm) ? 1 : 0;
            else if (funct3 == 4) rd_val = val1 ^ imm;
            else if (funct3 == 6) rd_val = val1 | imm;
            else if (funct3 == 7) rd_val = val1 & imm;
            else if (funct3 == 1) rd_val = val1 << (imm & 0x1F);
            else if (funct3 == 5) rd_val = (funct7 == 0x20) ? ((int32_t)val1 >> (imm & 0x1F)) : (val1 >> (imm & 0x1F));
            else write_reg = false;
            break;
        }
        case 0x37: { // LUI
            rd_val = inst & 0xFFFFF000;
            write_reg = true;
            break;
        }
        case 0x17: { // AUIPC
            rd_val = pc + (inst & 0xFFFFF000);
            write_reg = true;
            break;
        }
        case 0x6F: { // JAL
            int32_t imm = (((int32_t)inst) >> 31) << 20;
            imm |= ((inst >> 21) & 0x3FF) << 1;
            imm |= ((inst >> 20) & 0x1) << 11;
            imm |= ((inst >> 12) & 0xFF) << 12;
            rd_val = pc + 4;
            next_pc = pc + imm;
            write_reg = true;
            break;
        }
        case 0x67: { // JALR
            int32_t imm = ((int32_t)inst) >> 20;
            rd_val = pc + 4;
            next_pc = (regs[rs1] + imm) & ~1;
            write_reg = true;
            break;
        }
        case 0x63: { // Branches
            int32_t imm = (((int32_t)inst) >> 31) << 12;
            imm |= ((inst >> 7) & 0x1) << 11;
            imm |= ((inst >> 25) & 0x3F) << 5;
            imm |= ((inst >> 8) & 0xF) << 1;
            uint32_t v1 = regs[rs1], v2 = regs[rs2];
            bool take = false;
            if (funct3 == 0) take = (v1 == v2);
            else if (funct3 == 1) take = (v1 != v2);
            else if (funct3 == 4) take = ((int32_t)v1 < (int32_t)v2);
            else if (funct3 == 5) take = ((int32_t)v1 >= (int32_t)v2);
            else if (funct3 == 6) take = (v1 < v2);
            else if (funct3 == 7) take = (v1 >= v2);
            
            if (take) next_pc = pc + imm;
            break;
        }
        // Load/Store with full funct3 decoding
        case 0x03: { // Load: LB, LH, LW, LBU, LHU
            uint32_t addr = regs[rs1] + (((int32_t)inst) >> 20);
            if (addr < memory.size()) {
                if (funct3 == 0) { // LB: sign-extend byte
                    rd_val = (int32_t)(int8_t)memory[addr];
                    write_reg = true;
                } else if (funct3 == 1 && addr + 1 < memory.size()) { // LH: sign-extend halfword
                    uint16_t hword = memory[addr] | (memory[addr+1] << 8);
                    rd_val = (int32_t)(int16_t)hword;
                    write_reg = true;
                } else if (funct3 == 2 && addr + 3 < memory.size()) { // LW
                    rd_val = memory[addr] | (memory[addr+1]<<8) | (memory[addr+2]<<16) | (memory[addr+3]<<24);
                    write_reg = true;
                } else if (funct3 == 4) { // LBU: zero-extend byte
                    rd_val = memory[addr];
                    write_reg = true;
                } else if (funct3 == 5 && addr + 1 < memory.size()) { // LHU: zero-extend halfword
                    rd_val = memory[addr] | (memory[addr+1] << 8);
                    write_reg = true;
                } else { trapped = true; }
            } else { trapped = true; }
            break;
        }
        case 0x23: { // Store: SB, SH, SW
            // Reconstruct S-type immediate
            int32_t simm = (((int32_t)inst) >> 20) & ~0x1F;
            simm |= (inst >> 7) & 0x1F;
            uint32_t addr = regs[rs1] + simm;
            uint32_t v = regs[rs2];
            if (addr < memory.size()) {
                if (funct3 == 0) { // SB
                    memory[addr] = v & 0xFF;
                } else if (funct3 == 1 && addr + 1 < memory.size()) { // SH
                    memory[addr]   = v & 0xFF;
                    memory[addr+1] = (v >> 8) & 0xFF;
                } else if (funct3 == 2 && addr + 3 < memory.size()) { // SW
                    memory[addr]   = v & 0xFF;
                    memory[addr+1] = (v >> 8)  & 0xFF;
                    memory[addr+2] = (v >> 16) & 0xFF;
                    memory[addr+3] = (v >> 24) & 0xFF;
                } else { trapped = true; }
            } else { trapped = true; }
            break;
        }

        default:
            trapped = true; // Unimplemented instruction
            break;
    }

    if (write_reg && rd != 0 && !trapped) {
        regs[rd] = rd_val;
        update_crc(rd, rd_val);
    }
    
    if (!trapped) pc = next_pc;
}
