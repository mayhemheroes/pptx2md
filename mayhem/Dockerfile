# Build Stage
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    clang \
    && rm -rf /var/lib/apt/lists/*

RUN pip install atheris

ADD . /src
WORKDIR /src
RUN python3 -m pip install .

CMD ["/src/mayhem/fuzz_parser.py"]
