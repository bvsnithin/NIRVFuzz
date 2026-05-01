// =============================================================================
// top.v
// Top-level wrapper for the Verilator simulation.
// Instantiates:
//   - PicoRV32 (native interface, not AXI, for simpler signal tapping)
//   - Dual-Port BRAM (64KB)
//   - CRC-32 Register Snooper
//   - Toggle Monitor
//
// The testbench (sim_main.cpp) drives:
//   clk, rst_n
//   mem_a_* (Port A of BRAM — instruction/data loading)
//   cpu_resetn (holds PicoRV32 in reset while loading)
//
// The testbench reads back:
//   crc_out, toggle_count_out, done, trap
// =============================================================================

module top (
    input  wire        clk,
    input  wire        rst_n,

    // BRAM Port A (testbench loads instructions here)
    input  wire        mem_a_en,
    input  wire        mem_a_we,
    input  wire [3:0]  mem_a_be,
    input  wire [13:0] mem_a_addr,
    input  wire [31:0] mem_a_din,
    output wire [31:0] mem_a_dout,

    // CPU control
    input  wire        cpu_resetn,   // Active-low: hold low to keep CPU in reset

    // Fuzzer observation outputs
    output wire [31:0] crc_out,
    output wire [31:0] toggle_count_out,
    output wire [31:0] pc_out,
    output wire [31:0] insn_out,
    output wire        done,          // High for one cycle on EBREAK
    output wire        trap           // High if CPU trapped (illegal instruction etc)
);

    // -------------------------------------------------------------------------
    // PicoRV32 memory interface signals
    // -------------------------------------------------------------------------
    wire        mem_valid;
    wire        mem_instr;
    wire        mem_ready;
    wire [31:0] mem_addr;
    wire [31:0] mem_wdata;
    wire [3:0]  mem_wstrb;
    wire [31:0] mem_rdata;

    // -------------------------------------------------------------------------
    // PicoRV32 register write-back (internal signal tap)
    // These are exposed by picorv32 when ENABLE_REGS_DUALPORT=0 (default)
    // We use the `rvfi_*` signals or the direct internal wires.
    // For simplicity we use the RVFI (RISC-V Formal Interface) outputs.
    // -------------------------------------------------------------------------
    wire        rvfi_valid;
    wire [4:0]  rvfi_rd_addr;
    wire [31:0] rvfi_rd_wdata;
    wire [31:0] rvfi_insn;
    wire [31:0] rvfi_pc_rdata;

    // -------------------------------------------------------------------------
    // PicoRV32 instantiation
    // -------------------------------------------------------------------------
    picorv32 #(
        .ENABLE_COUNTERS     (1),
        .ENABLE_REGS_DUALPORT(1),
        .TWO_STAGE_SHIFT     (1),
        .ENABLE_MUL          (1),
        .ENABLE_DIV          (1),
        .ENABLE_FAST_MUL     (0),
        .COMPRESSED_ISA      (0),
        .ENABLE_IRQ          (0),
        .PROGADDR_RESET      (32'h0000_0000)
    ) cpu (
        .clk       (clk),
        .resetn    (cpu_resetn & rst_n),
        .trap      (trap),

        .mem_valid (mem_valid),
        .mem_instr (mem_instr),
        .mem_ready (mem_ready),
        .mem_addr  (mem_addr),
        .mem_wdata (mem_wdata),
        .mem_wstrb (mem_wstrb),
        .mem_rdata (mem_rdata),

        // RVFI outputs
        .rvfi_valid    (rvfi_valid),
        .rvfi_insn     (rvfi_insn),
        .rvfi_rd_addr  (rvfi_rd_addr),
        .rvfi_rd_wdata (rvfi_rd_wdata),
        .rvfi_pc_rdata (rvfi_pc_rdata),

        // Unused RVFI ports tied off
        .rvfi_order    (),
        .rvfi_trap     (),
        .rvfi_halt     (),
        .rvfi_intr     (),
        .rvfi_rs1_addr (),
        .rvfi_rs2_addr (),
        .rvfi_rs1_rdata(),
        .rvfi_rs2_rdata(),
        .rvfi_mem_addr (),
        .rvfi_mem_rmask(),
        .rvfi_mem_wmask(),
        .rvfi_mem_rdata(),
        .rvfi_mem_wdata(),
        .rvfi_pc_wdata ()
    );

    // -------------------------------------------------------------------------
    // BRAM instantiation
    // -------------------------------------------------------------------------
    wire [31:0] mem_b_dout;
    wire        mem_b_we = (mem_valid && |mem_wstrb);

    bram mem (
        .clk_a  (clk),
        .en_a   (mem_a_en),
        .we_a   (mem_a_we),
        .be_a   (mem_a_be),
        .addr_a (mem_a_addr),
        .din_a  (mem_a_din),
        .dout_a (mem_a_dout),

        .clk_b  (clk),
        .en_b   (mem_valid),
        .we_b   (mem_b_we),
        .be_b   (mem_wstrb),
        .addr_b (mem_addr[15:2]),   // Byte addr -> word addr
        .din_b  (mem_wdata),
        .dout_b (mem_b_dout)
    );

    reg mem_ready_reg;
    always @(posedge clk) begin
        if (!rst_n) mem_ready_reg <= 0;
        else mem_ready_reg <= mem_valid && !mem_ready_reg;
    end
    assign mem_ready = mem_ready_reg;
    assign mem_rdata = mem_b_dout;

    // -------------------------------------------------------------------------
    // Execution End Detection
    // -------------------------------------------------------------------------
    // Pulse done on EBREAK or TRAP so fuzzer always gets feedback
    assign done = (rvfi_valid && (rvfi_insn == 32'h00100073)) || trap;
    assign pc_out = rvfi_pc_rdata;
    assign insn_out = rvfi_insn;

    // -------------------------------------------------------------------------
    // CRC-32 Snooper instantiation
    // -------------------------------------------------------------------------
    crc32_snooper crc_snoop (
        .clk      (clk),
        .rst_n    (rst_n & cpu_resetn),
        .wr_en    (rvfi_valid & (rvfi_rd_addr != 5'b0)),  // Skip x0 writes
        .wr_addr  (rvfi_rd_addr),
        .wr_data  (rvfi_rd_wdata),
        .done_i   (done),
        .crc_out  (crc_out),
        .crc_live ()
    );

    // -------------------------------------------------------------------------
    // Toggle Monitor instantiation
    // -------------------------------------------------------------------------
    toggle_monitor tog_mon (
        .clk              (clk),
        .rst_n            (rst_n & cpu_resetn),
        .alu_out          (rvfi_pc_rdata),   // PC of retiring instruction (distinct from reg write data)
        .mem_addr         (mem_addr),
        .reg_wdata        (rvfi_rd_wdata),
        .instr_word       (rvfi_insn),
        .done_i           (done),
        .toggle_count_out (toggle_count_out),
        .toggle_count_live()
    );

endmodule