// =============================================================================
// crc32_snooper.v
// Monitors PicoRV32's register write-back bus and computes a running CRC-32
// over every register write that occurs during execution.
//
// Inputs tap directly onto PicoRV32 internal signals:
//   wr_en   - register write enable (latched_rd_wdata valid signal)
//   wr_addr - destination register index (rd)
//   wr_data - value being written
//
// The CRC accumulates across all writes in one test run.
// At EBREAK (done_i), the final CRC is latched into crc_out.
// Reset clears the accumulator for the next test.
// =============================================================================

module crc32_snooper (
    input  wire        clk,
    input  wire        rst_n,

    // Tapped from PicoRV32 write-back
    input  wire        wr_en,
    input  wire [4:0]  wr_addr,
    input  wire [31:0] wr_data,

    // Pulse high for one cycle when execution ends (EBREAK)
    input  wire        done_i,

    // Final latched CRC (stable after done_i)
    output reg  [31:0] crc_out,

    // Running CRC (for debug / waveform inspection)
    output wire [31:0] crc_live
);

    // -------------------------------------------------------------------------
    // CRC-32 (IEEE 802.3 polynomial: 0xEDB88320 reflected)
    // -------------------------------------------------------------------------
    reg [31:0] crc_reg;

    // Combine addr + data into 37-bit input word, then feed byte-by-byte
    // We process 5 bytes: [wr_addr(4:0) | wr_data(31:0)]
    wire [39:0] crc_input = {3'b000, wr_addr, wr_data};

    function [31:0] crc32_byte;
        input [31:0] crc_in;
        input [7:0]  byte_in;
        integer i;
        reg [31:0] c;
        reg        b;
        begin
            c = crc_in ^ {24'h0, byte_in};
            for (i = 0; i < 8; i = i + 1) begin
                b = c[0];
                c = c >> 1;
                if (b) c = c ^ 32'hEDB88320;
            end
            crc32_byte = c;
        end
    endfunction

    function [31:0] crc32_word40;
        input [31:0] crc_in;
        input [39:0] data;
        reg [31:0] c;
        begin
            c = crc_in;
            c = crc32_byte(c, data[7:0]);
            c = crc32_byte(c, data[15:8]);
            c = crc32_byte(c, data[23:16]);
            c = crc32_byte(c, data[31:24]);
            c = crc32_byte(c, data[39:32]);
            crc32_word40 = c;
        end
    endfunction

    // -------------------------------------------------------------------------
    // Accumulator
    // -------------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            crc_reg <= 32'hFFFFFFFF;
            crc_out <= 32'h0;
        end else begin
            if (wr_en)
                crc_reg <= crc32_word40(crc_reg, crc_input);

            if (done_i)
                crc_out <= crc_reg ^ 32'hFFFFFFFF;  // Final XOR per CRC-32 spec
        end
    end

    assign crc_live = crc_reg ^ 32'hFFFFFFFF;

endmodule