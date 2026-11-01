CC ?= cc
KERNEL ?= $(shell uname -r)
KDIR ?= /usr/lib/modules/$(KERNEL)/build
export PYTHONDONTWRITEBYTECODE=1

.PHONY: all modules test test-full clean archive
all: build/selector
build/selector: touchbar/selector.c touchbar/weather-view.h touchbar/news-view.h
	mkdir -p build
	$(CC) -std=c11 -O2 -Wall -Wextra -Werror $< -o $@ $$(pkg-config --cflags --libs libdrm cairo) -lm
modules:
	$(MAKE) -C drivers/legacy KDIR=$(KDIR)
	$(MAKE) -C drivers/graphics KDIR=$(KDIR)
test: all
	./build/selector --self-test-logic
	./build/selector --self-test-features
	PYTHONPATH=touchbar:tools python3 -m unittest discover -s tests -p 'test_*.py' -v
	PYTHONPATH=touchbar python3 tests/check_piano.py
	PYTHONPATH=touchbar python3 tests/check_piano_demo.py
	PYTHONPATH=touchbar python3 tests/check_piano_expression.py
test-full: test
	./build/selector --self-test
clean:
	rm -rf build
	$(MAKE) -C drivers/legacy KDIR=$(KDIR) clean
	$(MAKE) -C drivers/graphics KDIR=$(KDIR) clean
archive:
	python3 tools/release.py
