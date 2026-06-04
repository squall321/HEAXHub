# heaxhub_toolchain_polyglot.sif

Polyglot build toolchain SIF for HEAXHub integration_builder.

Serves these stacks (see `backend/app/services/toolchain_resolver.py`):

| Stack key         | Build command (run inside the SIF) |
|-------------------|------------------------------------|
| `dotnet_aspnet`   | `dotnet publish -c Release …`     |
| `java_springboot` | `./mvnw -B -DskipTests package` (or `mvn -B -DskipTests package`) |
| `rust_actix`      | `cargo build --release`           |

## Size warning

This SIF is **large — roughly 1.5 GB built**. Reasons:

- .NET 8 SDK tarball ≈ 230 MB extracted (~700 MB with runtimes after first restore)
- OpenJDK 17 JDK ≈ 350 MB
- Maven 3.9 ≈ 10 MB (the cached `.m2` repository on first build can dwarf this)
- Rust stable toolchain (rustc + cargo + std) ≈ 350 MB
- Ubuntu 22.04 base + `build-essential` + `libssl-dev` ≈ 250 MB

Plan disk budget accordingly:

- Build host: at least **3 GB free** during `apptainer build` (intermediate sandbox).
- Target hosts: at least **2 GB free** in `HEAXHUB_TOOLCHAIN_SIF_DIR` per copy of the SIF.
- If you also keep `nodejs20`, `python312`, and `go122` SIFs alongside this one, budget **~3 GB total** for the four toolchain SIFs.

## Build

```bash
# Online build host (needs outbound HTTPS to dotnet, apache, rust-lang)
sudo apptainer build heaxhub_toolchain_polyglot.sif \
    deploy/apptainer/toolchain_polyglot.def
```

Or use the wrapper (see `infra/packages/toolchains/build-toolchain.sh` when present):

```bash
./infra/packages/toolchains/build-toolchain.sh --only polyglot
```

## Deploy

```bash
# Copy to the operator-configured SIF directory:
scp heaxhub_toolchain_polyglot.sif \
    user@offline-host:${HEAXHUB_TOOLCHAIN_SIF_DIR}/
```

`HEAXHUB_TOOLCHAIN_SIF_DIR` defaults to `SIF_DIR`, which itself defaults to `deploy/apptainer/`. The integration_builder probes this path on **every** build call — no worker restart is required after dropping the file in.

## Quick verify (after build)

```bash
apptainer exec heaxhub_toolchain_polyglot.sif bash -lc \
    'dotnet --info && java -version && mvn -v && rustc --version && cargo --version'
```

Expected:

- `dotnet --info` reports SDK 8.0.x
- `java -version` reports OpenJDK 17
- `mvn -v` reports Apache Maven 3.9.x bound to Java 17
- `rustc --version` / `cargo --version` report stable channel
