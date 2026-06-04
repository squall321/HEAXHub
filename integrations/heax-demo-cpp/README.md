# heax-demo-cpp

C++ executable demo integration for HEAXHub.

## Build

```bash
make
```

Produces `bin/heax-demo-cpp` via `g++ -O2`.

## Run

```bash
./bin/heax-demo-cpp 10
```

Prints `HEAXHub C++ demo: count=<arg>` and writes `output/result.json`
containing `{"ran": "cpp", "count": <arg>}`.

## Inputs

| Name  | Type    | Default | Range      |
|-------|---------|---------|------------|
| count | integer | 10      | 1 – 10000  |
