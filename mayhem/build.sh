#!/usr/bin/env bash
#
# mayhem/build.sh — build the pptx2md atheris fuzz harness.
#
# pptx2md is pure Python (python-pptx + a handful of pure-python/wheel deps: lxml,
# Pillow, rapidfuzz, pydantic, tqdm). There is no project C/C++ to compile, so "build"
# means: (1) bake a wheelhouse of pptx2md's runtime deps + atheris (network ONLY the
# first time — a re-run, including a fully offline one, resolves from the on-disk
# wheelhouse), (2) create a venv and install those deps into it from that wheelhouse,
# (3) compile mayhem/embed_launcher.c, the ELF Mayhem's Mayhemfile points at. It EMBEDS
# the Python interpreter via the CPython C API and runs fuzz_parser.py IN THE SAME
# PROCESS (pptx2md itself is imported straight off the checkout via sys.path — no
# packaging step, so a later source PATCH takes effect immediately with no rebuild of
# pptx2md itself; only the launcher, which never touches pptx2md's own code, is compiled).
# We do NOT exec() into a separate python3 for this: exec() swaps the whole process image
# out from under Mayhem's coverage collector — the fuzzer runs fine but edges_covered
# comes back 0 even though atheris/libFuzzer iterates correctly (verified: this
# integration's first cut did exactly that). Embedding keeps one continuous process from
# launch through atheris's own libFuzzer driver loop, so its coverage counters (loaded
# into that same process) are visible to Mayhem.
#
# $SANITIZER_FLAGS / $DEBUG_FLAGS (exported by the base image, see the contract below)
# are threaded into the wheelhouse build step as CFLAGS/CXXFLAGS/LDFLAGS: atheris and
# pptx2md's deps ship manylinux wheels for our python (so these are normally inert),
# but honoring them means any dependency that DOES need a source build (no compatible
# wheel) still gets ASan/UBSan + DWARF<4 instrumentation instead of silently skipping it.
# The launcher itself also carries DEBUG_FLAGS directly (DWARF <= 3, §6.2 item 10) — it's
# the one binary here we compile ourselves (libpython/atheris are dynamically loaded
# .so's, contributing no CUs into the launcher's own ELF, so its .debug_info is exactly
# our one DWARF-3 CU, no anchor needed).
set -euo pipefail

[ -n "${SOURCE_DATE_EPOCH:-}" ] || unset SOURCE_DATE_EPOCH
: "${SANITIZER_FLAGS=-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer}"
: "${DEBUG_FLAGS:=-g -gdwarf-3}"
: "${CC:=clang}" ; : "${CXX:=clang++}"
: "${MAYHEM_JOBS:=$(nproc)}"
export SANITIZER_FLAGS DEBUG_FLAGS CC CXX MAYHEM_JOBS

cd "$SRC"

WHEELHOUSE=/mayhem/wheelhouse
FUZZ_VENV=/mayhem/fuzz-venv

# pptx2md's runtime deps (pyproject.toml [tool.poetry.dependencies]) + atheris. Listed
# by name (no version pins) so the wheelhouse always resolves the latest compatible
# wheel for the image's python/arch; pptx2md ITSELF is never pip-installed — it's
# imported straight from $SRC via sys.path (see embed_launcher.c), so a PATCH to its
# source needs no reinstall.
DEPS="python-pptx rapidfuzz Pillow tqdm pydantic scipy numpy atheris"

# 1) Populate the wheelhouse (needs the network) ONLY the first time — this is what
#    makes the re-run at the PATCH tier (§6.2 item 9) and the air-gap check (§6.5)
#    succeed: once baked into the image layer, every later `pip install` below is
#    `--no-index --find-links=$WHEELHOUSE`, so it never touches the network again.
mkdir -p "$WHEELHOUSE"
if [ -z "$(ls -A "$WHEELHOUSE" 2>/dev/null)" ]; then
  python3 -m pip download --dest "$WHEELHOUSE" --no-input pip setuptools wheel
  CFLAGS="$SANITIZER_FLAGS $DEBUG_FLAGS" CXXFLAGS="$SANITIZER_FLAGS $DEBUG_FLAGS" LDFLAGS="$SANITIZER_FLAGS" \
    python3 -m pip download --dest "$WHEELHOUSE" --no-input $DEPS
fi

# 2) Build/rebuild the fuzz venv purely from the wheelhouse (offline-safe, idempotent).
rm -rf "$FUZZ_VENV"
python3 -m venv "$FUZZ_VENV"
"$FUZZ_VENV/bin/pip" install --no-index --find-links="$WHEELHOUSE" -q pip setuptools wheel
"$FUZZ_VENV/bin/pip" install --no-index --find-links="$WHEELHOUSE" -q $DEPS

# 3) The embedding launcher ELF Mayhem's Mayhemfile points at (see mayhem/embed_launcher.c).
# Uses the fuzz-venv's own python3 to derive the exact include/lib flags + base prefix
# (PY_HOME) so the embedded interpreter matches the venv that has atheris/deps installed.
PYBIN="$FUZZ_VENV/bin/python3"
PY_INC="$("$PYBIN" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
PY_LIBDIR="$("$PYBIN" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
PY_ABI="$("$PYBIN" -c 'import sysconfig; print(sysconfig.get_config_var("LDVERSION") or sysconfig.get_config_var("VERSION"))')"
PY_HOME="$("$PYBIN" -c 'import sys; print(sys.base_prefix)')"
$CC $DEBUG_FLAGS -O0 "-I$PY_INC" "-DPY_HOME=\"$PY_HOME\"" \
    "$SRC/mayhem/embed_launcher.c" -o /mayhem/fuzz_parser \
    "-L$PY_LIBDIR" "-lpython$PY_ABI" -lpthread -ldl -lutil
