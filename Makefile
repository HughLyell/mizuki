PREFIX ?= /usr/local
PYTHON ?= python3

.PHONY: run test lint build install flatpak clean

run:
	./run.sh

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

lint:
	PYTHONPATH=src $(PYTHON) -m compileall -q src tests

build:
	@test -d build || meson setup build --prefix=$(PREFIX)
	meson compile -C build

install: build
	meson install -C build

flatpak:
	flatpak-builder --force-clean --user --install build-aux/build build-aux/io.github.hughlyell.Mizuki.yml

clean:
	rm -rf build build-aux/build
	find src tests -name '__pycache__' -type d -prune -exec rm -rf {} +
