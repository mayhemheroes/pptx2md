#!/usr/bin/env python3
# Fuzz harness for pptx2md's real conversion entrypoint: it drives the same
# path the `pptx2md` CLI takes (pptx2md.entry.convert -> utils.load_pptx ->
# parser.parse -> an outputter.Formatter), but with fuzzer-controlled bytes as
# the .pptx file (a zip/OOXML container) and fuzzer-controlled ConversionConfig
# knobs (output format, multi-column detection, notes/image/color/escaping
# toggles). This exercises python-pptx's OOXML/zip parsing plus pptx2md's own
# slide-walking, multi-column heuristics, and every output formatter.

import sys
import tempfile
from pathlib import Path

import atheris
import fuzz_helpers

# Pre-import the heavy general-purpose dependencies UNINSTRUMENTED (python-pptx, pydantic,
# lxml, Pillow, rapidfuzz, tqdm). atheris.instrument_imports() instruments every NEW module
# pulled in transitively during its block; pydantic alone has a large internal module tree,
# and instrumenting it (plus python-pptx/lxml/Pillow) adds tens of seconds of one-time
# startup cost — long enough to blow past Mayhem's per-run smoketest timeout before a single
# fuzz input ever executes (observed: "has_critical_errors"/0-edge "Run Failed" in the real
# cloud run despite fuzzing fine once started). Importing them here, before the instrumented
# block, means atheris skips them (already loaded) and instruments ONLY pptx2md's own code
# below — the actual code under test — keeping coverage meaningful without the startup tax.
import pptx  # noqa: F401
import pydantic  # noqa: F401
import lxml.etree  # noqa: F401
import PIL  # noqa: F401
import rapidfuzz  # noqa: F401
import tqdm  # noqa: F401
import numpy  # noqa: F401
import scipy  # noqa: F401
import scipy.optimize  # noqa: F401 - pulls in scipy's own (very large) submodule tree

with atheris.instrument_imports():
    from pptx2md.parser import parse
    from pptx2md.types import ConversionConfig
    from pptx2md.utils import load_pptx
    import pptx2md.outputter as outputter

from zipfile import BadZipFile
import struct
import zlib

from lxml.etree import XMLSyntaxError
from pptx.exc import PackageNotFoundError

_OUTPUTTERS = [
    outputter.MarkdownFormatter,
    outputter.WikiFormatter,
    outputter.MadokoFormatter,
    outputter.QuartoFormatter,
]

# Malformed .pptx bytes legitimately surface as these from python-pptx's
# zip/XML/part-relationship parsing or pptx2md's own slide walking; anything
# else is an unexpected crash and must propagate.
_EXPECTED = (
    BadZipFile,
    PackageNotFoundError,
    XMLSyntaxError,
    zlib.error,
    struct.error,
    UnicodeDecodeError,
    EOFError,
    KeyError,
    IndexError,
    NotImplementedError,
)


@atheris.instrument_func
def TestOneInput(data):
    if len(data) < 64:
        return

    fdp = fuzz_helpers.EnhancedFuzzedDataProvider(data)

    # Reserve a byte up front to pick the output format / feature flags before
    # consuming the rest of the buffer as the raw .pptx content, so the choice
    # doesn't shrink/perturb the file bytes handed to python-pptx.
    selector = fdp.ConsumeIntInRange(0, 255)
    outputter_cls = _OUTPUTTERS[selector % len(_OUTPUTTERS)]
    try_multi_column = bool(selector & 0x10)
    disable_notes = bool(selector & 0x20)
    disable_image = bool(selector & 0x40)
    keep_similar_titles = bool(selector & 0x80)

    pptx_bytes = fdp.ConsumeRemainingBytes()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pptx_path = tmp / 'input.pptx'
        pptx_path.write_bytes(pptx_bytes)

        config = ConversionConfig(
            pptx_path=pptx_path,
            output_path=tmp / 'out.md',
            image_dir=tmp / 'img',
            disable_image=disable_image,
            disable_notes=disable_notes,
            try_multi_column=try_multi_column,
            keep_similar_titles=keep_similar_titles,
        )

        try:
            prs = load_pptx(str(pptx_path))
            ast = parse(config, prs)
            out = outputter_cls(config)
            out.output(ast)
        except _EXPECTED:
            return
        except ValueError as e:
            if 'seek' in str(e) or 'columns' in str(e):
                return
            raise
        except RuntimeError as e:
            if 'encrypted' in str(e):
                return
            raise


def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == '__main__':
    main()
