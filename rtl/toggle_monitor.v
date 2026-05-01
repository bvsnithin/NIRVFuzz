// =============================================================================
// toggle_monitor.v
// Counts bit-flips (0->1 and 1->0 transitions) on monitored internal buses
// of PicoRV32 every clock cycle. The accumulated count is the "Toggle Score"
// used as the grey-box feedback signal for the genetic algorithm.
//
// Monitored signals (tap these from PicoRV32 internals):
//   alu_out    - ALU result bus (32-bit)
//   mem_addr   - memory address bus (32-bit)
//   reg_wdata  - register write-back data (32-bit)
//   instr_word - currently executing instruction word (32-bit)
//
// A high toggle score means the instruction sequence exercised many distinct
// microarchitectural states — exactly the sequences worth mutating further.
// =============================================================================

module toggle_monitor (
    input  wire        clk,
    input  wire        rst_n,

    // Tapped buses from PicoRV32
    input  wire [31:0] alu_out,
    input  wire [31:0] mem_addr,
    input  wire [31:0] reg_wdata,
    input  wire [31:0] instr_word,

    // Pulse high for one cycle when execution ends
    input  wire        done_i,

    // Latched toggle count (stable after done_i)
    output reg  [31:0] toggle_count_out,

    // Running count (for waveform inspection)
    output wire [31:0] toggle_count_live
);

    // -------------------------------------------------------------------------
    // Previous-cycle registers
    // -------------------------------------------------------------------------
    reg [31:0] prev_alu;
    reg [31:0] prev_mem;
    reg [31:0] prev_wdata;
    reg [31:0] prev_instr;

    // -------------------------------------------------------------------------
    // Toggle accumulator
    // -------------------------------------------------------------------------
    reg [31:0] acc;

    // Count set bits in a 32-bit word (popcount)
    function [5:0] popcount32;
        input [31:0] w;
        integer i;
        reg [5:0] cnt;
        begin
            cnt = 0;
            for (i = 0; i < 32; i = i + 1)
                cnt = cnt + w[i];
            popcount32 = cnt;
        end
    endfunction

    wire [31:0] xor_alu   = alu_out   ^ prev_alu;
    wire [31:0] xor_mem   = mem_addr  ^ prev_mem;
    wire [31:0] xor_wdata = reg_wdata ^ prev_wdata;
    wire [31:0] xor_instr = instr_word ^ prev_instr;

    wire [7:0] flips_this_cycle =
        {2'b00, popcount32(xor_alu)}   +
        {2'b00, popcount32(xor_mem)}   +
        {2'b00, popcount32(xor_wdata)} +
        {2'b00, popcount32(xor_instr)};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prev_alu          <= 32'h0;
            prev_mem          <= 32'h0;
            prev_wdata        <= 32'h0;
            prev_instr        <= 32'h0;
            acc               <= 32'h0;
            toggle_count_out  <= 32'h0;
        end else begin
            // Capture previous values
            prev_alu   <= alu_out;
            prev_mem   <= mem_addr;
            prev_wdata <= reg_wdata;
            prev_instr <= instr_word;

            // Accumulate flips
            acc <= acc + {24'h0, flips_this_cycle};

            // Latch on done
            if (done_i)
                toggle_count_out <= acc;
        end
    end

    assign toggle_count_live = acc;

endmodule