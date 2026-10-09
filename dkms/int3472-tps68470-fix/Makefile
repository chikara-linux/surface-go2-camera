obj-m := intel_skl_int3472_tps68470.o
intel_skl_int3472_tps68470-y := tps68470.o tps68470_board_data.o

KVER ?= $(shell uname -r)
KDIR ?= /lib/modules/$(KVER)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
