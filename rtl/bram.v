// =============================================================================
// bram.v
// Synchronous true dual-port BRAM (64KB)
// Port A : testbench / ARM replacement (read + write)
// Port B : PicoRV32 instruction + data memory (read + write)
//
// Both ports are 32-bit wide, word-addressed.
// Byte-enable on both ports.
// =============================================================================

module bram #(
    parameter DEPTH = 16384   // 16384 x 32-bit = 64 KB
)(
    // Port A (testbench side)
    input  wire        clk_a,
    input  wire        en_a,
    input  wire        we_a,
    input  wire [3:0]  be_a,
    input  wire [13:0] addr_a,
    input  wire [31:0] din_a,
    output reg  [31:0] dout_a,

    // Port B (PicoRV32 side)
    input  wire        clk_b,
    input  wire        en_b,
    input  wire        we_b,
    input  wire [3:0]  be_b,
    input  wire [13:0] addr_b,
    input  wire [31:0] din_b,
    output reg  [31:0] dout_b
);

    // Memory array — 4 byte-wide banks for byte-enable support
    reg [7:0] mem0 [0:DEPTH-1];
    reg [7:0] mem1 [0:DEPTH-1];
    reg [7:0] mem2 [0:DEPTH-1];
    reg [7:0] mem3 [0:DEPTH-1];

    // -------------------------------------------------------------------------
    // Port A
    // -------------------------------------------------------------------------
    always @(posedge clk_a) begin
        if (en_a) begin
            if (we_a) begin
                if (be_a[0]) mem0[addr_a] <= din_a[7:0];
                if (be_a[1]) mem1[addr_a] <= din_a[15:8];
                if (be_a[2]) mem2[addr_a] <= din_a[23:16];
                if (be_a[3]) mem3[addr_a] <= din_a[31:24];
            end
            dout_a <= {mem3[addr_a], mem2[addr_a], mem1[addr_a], mem0[addr_a]};
        end
    end

    // -------------------------------------------------------------------------
    // Port B
    // -------------------------------------------------------------------------
    always @(posedge clk_b) begin
        if (en_b) begin
            if (we_b) begin
                if (be_b[0]) mem0[addr_b] <= din_b[7:0];
                if (be_b[1]) mem1[addr_b] <= din_b[15:8];
                if (be_b[2]) mem2[addr_b] <= din_b[23:16];
                if (be_b[3]) mem3[addr_b] <= din_b[31:24];
            end
            dout_b <= {mem3[addr_b], mem2[addr_b], mem1[addr_b], mem0[addr_b]};
        end
    end

endmodule