#!/usr/bin/env python3

import atheris
import sys
import fuzz_helpers
from pptx import Presentation

with atheris.instrument_imports():
    from pptx2md.parser import parse
    import pptx2md.outputter as outputter

# Exceptions
from zipfile import BadZipfile
import zlib
import struct

outputter_classes = [outputter.wiki_outputter, outputter.madoko_outputter, outputter.md_outputter]


def TestOneInput(data):
    fdp = fuzz_helpers.EnhancedFuzzedDataProvider(data)
    if len(data) < 200:
        return -1

    try:
        # Don't want to consume data and invalidate a pptx file, so just use the 199th byte arbitratily to pick
        outputter = outputter_classes[data[198] % len(outputter_classes)]('/dev/null')
        with fdp.ConsumeMemoryFile(all_data=True, as_bytes=True) as f:
            pres = Presentation(f)
            parse(pres, outputter)
    except (SystemExit, BadZipfile, NotImplementedError, zlib.error, struct.error,
            UnicodeDecodeError, EOFError) as e:
        return -1
    except ValueError as e:
        if 'seek' in str(e):
            return -1
        raise
    except Exception as e:
        print(type(e))
        raise e
    except RuntimeError as e:
        if 'encrypted' in str(e):
            return -1
        raise


def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
