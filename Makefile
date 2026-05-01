VERILATOR = verilator
VFLAGS = -Wall -Wno-fatal --trace --build -cc -O3 -DRISCV_FORMAL --top-module top
CPP_SRCS = sim/sim_main.cpp sim/golden_model.cpp
RTL_COMMON = rtl/top.v rtl/bram.v rtl/crc32_snooper.v rtl/toggle_monitor.v

all: sim/Vtop sim/Vtop_buggy

# Build Clean Model
sim/Vtop: $(RTL_COMMON) rtl/picorv32.v $(CPP_SRCS)
	mkdir -p build/clean
	$(VERILATOR) $(VFLAGS) --Mdir build/clean --prefix Vtop \
	-exe $(CPP_SRCS) rtl/picorv32.v $(RTL_COMMON) \
	-o ../../sim/Vtop

# Build Buggy Model
sim/Vtop_buggy: $(RTL_COMMON) rtl/buggy_picorv32.v $(CPP_SRCS)
	mkdir -p build/buggy
	$(VERILATOR) $(VFLAGS) --Mdir build/buggy --prefix Vtop \
	-exe $(CPP_SRCS) rtl/buggy_picorv32.v $(RTL_COMMON) \
	-o ../../sim/Vtop_buggy

fuzz: all
	cd fuzzer && python3 fuzzer.py

clean:
	rm -rf build sim/Vtop sim/Vtop_buggy fuzzer/crashes seed_corpus fuzzer/tmp_*.bin
