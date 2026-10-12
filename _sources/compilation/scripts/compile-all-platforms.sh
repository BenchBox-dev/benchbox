#!/bin/bash

# Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

CONTAINER_ENGINE="${BENCHBOX_CONTAINER_ENGINE:-}"
if [ -z "$CONTAINER_ENGINE" ]; then
    for _engine in container docker podman mocker; do
        if command -v "$_engine" >/dev/null 2>&1; then
            if [ "$_engine" = "container" ] && ! container system status >/dev/null 2>&1; then
                continue
            fi
            CONTAINER_ENGINE="$_engine"
            break
        fi
    done
fi

_host_arch() {
    case "$(uname -m)" in
        arm64 | aarch64) echo "arm64" ;;
        x86_64 | amd64) echo "x86_64" ;;
        *) uname -m ;;
    esac
}

_assert_engine_supports_arch() {
    local target_arch=$1

    [ "$target_arch" = "$(_host_arch)" ] && return 0

    if [ "$CONTAINER_ENGINE" = "mocker" ]; then
        log_error "mocker cannot execute ${target_arch} images on $(_host_arch)."
        log_info "Install Apple 'container' (emulates amd64 via Rosetta) or Docker,"
        log_info "or select one explicitly with BENCHBOX_CONTAINER_ENGINE=<engine>."
        exit 1
    fi
}

_engine_run_flags() {
    local target_arch=$1

    [ "$target_arch" = "$(_host_arch)" ] && return 0

    if [ "$CONTAINER_ENGINE" = "container" ]; then
        printf '%s' "--rosetta"
    fi
}

PLATFORMS=(
    "darwin:arm64:native"
    "darwin:x86_64:cross"
    "linux:x86_64:docker"
    "linux:arm64:docker"
    "windows:x86_64:docker"
    "windows:arm64:docker"
)

compile_tpch_macos_native() {
    log_info "Compiling TPC-H natively for macOS ARM64..."

    local platform_dir="$PROJECT_ROOT/_binaries/tpc-h/darwin-arm64"
    local temp_build="/tmp/tpch-build-native-$$"

    mkdir -p "$temp_build"
    cp -r "$PROJECT_ROOT/_sources/tpc-h/dbgen"/* "$temp_build/"

    cd "$temp_build"
    make -f makefile.suite clean || true
    make -f makefile.suite CC=clang MACHINE=LINUX DATABASE=ORACLE WORKLOAD=TPCH

    cp dbgen qgen dists.dss "$platform_dir/"

    cd "$platform_dir"
    if command -v md5 >/dev/null 2>&1; then
        md5 -r dbgen qgen dists.dss > checksums.md5
    else
        md5sum dbgen qgen dists.dss > checksums.md5
    fi

    rm -rf "$temp_build"

    log_success "TPC-H native macOS ARM64 compilation completed"
}

_deploy_tpcds_to_package() {
    local platform_name="$1"
    local source_dir="$PROJECT_ROOT/_binaries/tpc-ds/${platform_name}"
    local package_dir="$PROJECT_ROOT/benchbox/_binaries/tpc-ds/${platform_name}"

    local exe_ext=""
    if echo "$platform_name" | grep -q "windows"; then
        exe_ext=".exe"
    fi

    log_info "Deploying TPC-DS binaries to package dir: benchbox/_binaries/tpc-ds/${platform_name}"
    mkdir -p "$package_dir"

    cp "$source_dir/dsdgen${exe_ext}"  "$package_dir/dsdgen${exe_ext}"
    cp "$source_dir/dsqgen${exe_ext}"  "$package_dir/dsqgen${exe_ext}"
    cp "$source_dir/tpcds.dst"         "$package_dir/tpcds.dst"
    cp "$source_dir/tpcds.idx"         "$package_dir/tpcds.idx"
    if [ -z "$exe_ext" ]; then
        chmod +x "$package_dir/dsdgen" "$package_dir/dsqgen"
    fi

    cd "$package_dir"
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "dsdgen${exe_ext}" "dsqgen${exe_ext}" tpcds.dst tpcds.idx > checksums.md5
    else
        md5 -r "dsdgen${exe_ext}" "dsqgen${exe_ext}" tpcds.dst tpcds.idx > checksums.md5
    fi
    cd "$PROJECT_ROOT"

    log_success "Deployed to benchbox/_binaries/tpc-ds/${platform_name}"
}

compile_tpcds_macos_native() {
    log_info "Compiling TPC-DS natively for macOS ARM64..."

    local platform_dir="$PROJECT_ROOT/_binaries/tpc-ds/darwin-arm64"
    local temp_build="/tmp/tpcds-build-native-$$"

    mkdir -p "$temp_build"
    cp -r "$PROJECT_ROOT/_sources/tpc-ds/tools"/* "$temp_build/"

    cd "$temp_build"

    if ! grep -q "#include <limits.h>" genrand.c; then
        sed -i '' '1i\
#include <limits.h>
' genrand.c
    fi

    make clean || true

    make CC=clang distcomp CFLAGS="-O2 -DMACOS -DMAXINT=INT_MAX -fcommon" LDFLAGS=""
    ./distcomp -i tpcds.dst -o tpcds.idx

    make CC=clang dsdgen dsqgen CFLAGS="-O2 -DMACOS -DMAXINT=INT_MAX -fcommon" LDFLAGS=""

    cp dsdgen dsqgen tpcds.dst tpcds.idx "$platform_dir/"
    cp -r "$PROJECT_ROOT/_sources/tpc-ds/query_templates" "$platform_dir/"

    cd "$platform_dir"
    find . -type f -name "*.dst" -o -name "*.idx" -o -name "*gen" | xargs md5 -r > checksums.md5

    rm -rf "$temp_build"

    _deploy_tpcds_to_package "darwin-arm64"

    log_success "TPC-DS native macOS ARM64 compilation completed"
}

compile_tpch_macos_cross() {
    local target_arch="$1"
    log_info "Cross-compiling TPC-H for macOS ${target_arch}..."

    local platform_dir="$PROJECT_ROOT/_binaries/tpc-h/darwin-${target_arch}"
    mkdir -p "$platform_dir"

    local build_dir="/tmp/tpch-cross-${target_arch}-$$"
    rm -rf "$build_dir"
    cp -r "$PROJECT_ROOT/_sources/tpc-h/dbgen" "$build_dir"

    cd "$build_dir"

    local cross_flags=""
    if [ "$target_arch" = "x86_64" ]; then
        cross_flags="-arch x86_64"
    fi

    make -f makefile.suite clean || true
    make -f makefile.suite \
        CC="clang $cross_flags" \
        MACHINE=LINUX \
        DATABASE=ORACLE \
        WORKLOAD=TPCH \
        CFLAGS="-g -DDBNAME=\\\"dss\\\" -DLINUX -DORACLE -DTPCH -DRNG_TEST -D_FILE_OFFSET_BITS=64 -DEOL_HANDLING" \
        LDFLAGS="$cross_flags -O" \
        || {
            log_error "TPC-H cross-compilation failed for macOS ${target_arch}"
            rm -rf "$build_dir"
            return 1
        }

    cp dbgen qgen dists.dss "$platform_dir/"
    chmod +x "$platform_dir/dbgen" "$platform_dir/qgen"

    cd "$platform_dir"
    md5 * > checksums.md5

    rm -rf "$build_dir"

    log_success "TPC-H cross-compilation completed for macOS ${target_arch}"
}

compile_tpcds_macos_cross() {
    local target_arch="$1"
    log_info "Cross-compiling TPC-DS for macOS ${target_arch}..."

    local platform_dir="$PROJECT_ROOT/_binaries/tpc-ds/darwin-${target_arch}"
    mkdir -p "$platform_dir"

    local build_dir="/tmp/tpcds-cross-${target_arch}-$$"
    rm -rf "$build_dir"
    cp -r "$PROJECT_ROOT/_sources/tpc-ds/tools" "$build_dir"

    cd "$build_dir"

    local cross_flags=""
    if [ "$target_arch" = "x86_64" ]; then
        cross_flags="-arch x86_64"
    fi

    sed -i '' '1i\
#include <limits.h>
' genrand.c

    make clean || true

    make distcomp \
        CC="clang $cross_flags" \
        CFLAGS="-O2 -DMACOS -DMAXINT=INT_MAX -fcommon $cross_flags" \
        LDFLAGS="$cross_flags" \
        || {
            log_error "TPC-DS distcomp cross-compilation failed for macOS ${target_arch}"
            rm -rf "$build_dir"
            return 1
        }

    ./distcomp -i tpcds.dst -o tpcds.idx

    make dsdgen dsqgen \
        CC="clang $cross_flags" \
        CFLAGS="-O2 -DMACOS -DMAXINT=INT_MAX -fcommon $cross_flags" \
        LDFLAGS="$cross_flags" \
        || {
            log_error "TPC-DS cross-compilation failed for macOS ${target_arch}"
            rm -rf "$build_dir"
            return 1
        }

    cp dsdgen dsqgen tpcds.dst tpcds.idx "$platform_dir/"
    cp -r "$PROJECT_ROOT/_sources/tpc-ds/query_templates" "$platform_dir/"
    chmod +x "$platform_dir/dsdgen" "$platform_dir/dsqgen"

    cd "$platform_dir"
    find . -type f -name '*.dst' -o -name '*.idx' -o -name '*gen' | xargs md5 > checksums.md5

    rm -rf "$build_dir"

    _deploy_tpcds_to_package "darwin-${target_arch}"

    log_success "TPC-DS cross-compilation completed for macOS ${target_arch}"
}

compile_with_docker() {
    local platform=$1
    local arch=$2
    local benchmark=$3

    local benchmark_upper=$(echo "$benchmark" | tr '[:lower:]' '[:upper:]')
    log_info "Compiling ${benchmark_upper} for ${platform}-${arch} using ${CONTAINER_ENGINE}..."

    _assert_engine_supports_arch "$arch"

    local platform_dir="$PROJECT_ROOT/_binaries/${benchmark}/${platform}-${arch}"

    local image_name="benchbox/${benchmark}-${platform}-${arch}"
    "$CONTAINER_ENGINE" build -f "$PROJECT_ROOT/_sources/compilation/docker/Dockerfile.${platform}-${arch}" \
                 -t "$image_name" \
                 "$PROJECT_ROOT/_sources/compilation/docker/"

    if [ "$benchmark" = "tpc-h" ]; then
        compile_tpch_docker "$platform" "$arch" "$image_name" "$platform_dir"
    else
        compile_tpcds_docker "$platform" "$arch" "$image_name" "$platform_dir"
        _deploy_tpcds_to_package "${platform}-${arch}"
    fi
}

compile_tpch_docker() {
    local platform=$1
    local arch=$2
    local image_name=$3
    local platform_dir=$4

    "$CONTAINER_ENGINE" run --rm $(_engine_run_flags "$arch") \
        -v "$PROJECT_ROOT/_sources/tpc-h/dbgen:/build/source" \
        -v "$platform_dir:/build/output" \
        "$image_name" \
        bash -c "
            cd /build/source
            make -f makefile.suite clean || true

            # Framing note: these builds do NOT override CFLAGS, so they inherit
            # makefile.suite's default -DEOL_HANDLING (no trailing field
            # separator) -- BenchBox's canonical convention. Keep it that way so
            # Linux/Windows .tbl framing matches macOS.
            # Set compilation parameters based on platform
            if [ '$platform' = 'windows' ]; then
                # Fix MinGW integer literal compatibility in config.h
                sed -i 's/6364136223846793005uI64/6364136223846793005ULL/g' config.h
                sed -i 's/1uI64/1ULL/g' config.h

                # Windows cross-compilation with MinGW
                make -f makefile.suite CC=\$CROSS_CC MACHINE=\$CROSS_MACHINE DATABASE=\$CROSS_DATABASE WORKLOAD=\$CROSS_WORKLOAD
                cp dbgen.exe qgen.exe dists.dss /build/output/ || cp dbgen qgen dists.dss /build/output/
            else
                # Linux compilation
                make -f makefile.suite CC=gcc MACHINE=LINUX DATABASE=ORACLE WORKLOAD=TPCH
                cp dbgen qgen dists.dss /build/output/
            fi

            cd /build/output
            md5sum * > checksums.md5 2>/dev/null || true
        "

    log_success "TPC-H Docker compilation completed for ${platform}-${arch}"
}

compile_tpcds_docker() {
    local platform=$1
    local arch=$2
    local image_name=$3
    local platform_dir=$4

    "$CONTAINER_ENGINE" run --rm $(_engine_run_flags "$arch") \
        -v "$PROJECT_ROOT/_sources/tpc-ds/tools:/build/source" \
        -v "$PROJECT_ROOT/_sources/tpc-ds/query_templates:/build/query_templates" \
        -v "$platform_dir:/build/output" \
        "$image_name" \
        bash -c "
            cd /build/source

            # Apply TPC-DS compilation fixes based on research
            # Fix 1: Add limits.h include for MAXINT
            if ! grep -q '#include <limits.h>' genrand.c; then
                sed -i '1i#include <limits.h>' genrand.c
            fi

            # Set compilation parameters based on platform
            make clean || true
            if [ '$platform' = 'windows' ]; then
                # Windows cross-compilation strategy:
                # mkheader and distcomp are build-time host tools that generate header files
                # and the tpcds.idx index. They must run natively on the build host (Linux),
                # so we compile them with native GCC first, run them, then cross-compile
                # dsdgen/dsqgen for Windows with MinGW.

                # Step 1: Build native host tools (mkheader, distcomp) with native GCC
                make CC=gcc mkheader CFLAGS='-O2 -DLINUX -DMAXINT=INT_MAX -fcommon'
                ./mkheader column_list.txt

                make CC=gcc distcomp CFLAGS='-O2 -DLINUX -DMAXINT=INT_MAX -fcommon'
                ./distcomp -i tpcds.dst -o tpcds.idx

                # Step 2: Remove only .o files so MinGW rebuilds them cleanly.
                # Keeping generated headers (tables.h, columns.h, streams.h, tpcds.idx.h)
                # prevents Make from trying to re-run mkheader or distcomp as Windows .exe.
                rm -f *.o

                # Step 3: Cross-compile dsdgen/dsqgen for Windows with MinGW.
                # -o distcomp: treat distcomp as already-built (old) - prevents Make from
                #   trying to cross-link distcomp.exe which we don't need.
                # LIBS overrides the Makefile's link-time libs; -lws2_32 provides ntohl
                #   (from dist.c) and must appear after the object files.
                make CC=\$CROSS_CC -o distcomp dsdgen dsqgen \
                  CFLAGS='-O2 -DWIN32 -DMAXINT=INT_MAX -fcommon' \
                  LDFLAGS='-static' \
                  LIBS='-lws2_32 -lm'

                cp dsdgen.exe dsqgen.exe tpcds.dst tpcds.idx /build/output/
            else
                # Linux compilation with GCC 9 for TPC-DS compatibility
                # First build distcomp to generate tpcds.idx
                make CC=gcc-9 distcomp CFLAGS='-O2 -DLINUX -DMAXINT=INT_MAX -fcommon' LDFLAGS='-static'
                ./distcomp -i tpcds.dst -o tpcds.idx

                # Now build main tools
                make CC=gcc-9 dsdgen dsqgen CFLAGS='-O2 -DLINUX -DMAXINT=INT_MAX -fcommon' LDFLAGS='-static'
                cp dsdgen dsqgen tpcds.dst tpcds.idx /build/output/
            fi

            # Copy query templates
            cp -r /build/query_templates /build/output/

            # Generate checksums
            cd /build/output
            find . -maxdepth 1 -type f \( -name '*.exe' -o -name '*.dst' -o -name '*.idx' -o -name '*gen' \) | sort | xargs md5sum > checksums.md5 2>/dev/null || true
        "

    log_success "TPC-DS Docker compilation completed for ${platform}-${arch}"
}

compile_native_only() {
    log_info "Compiling for native platform only (no Docker required)..."
    cd "$PROJECT_ROOT"

    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
        compile_tpch_macos_native
        compile_tpcds_macos_native
    elif [ "$(uname -s)" = "Darwin" ]; then
        compile_tpch_macos_cross "$(uname -m)"
        compile_tpcds_macos_cross "$(uname -m)"
    else
        log_error "Native-only mode is only supported on macOS. Use full compile mode for Linux/Windows."
        exit 1
    fi

    log_success "Native compilation complete."
}

compile_all_platforms() {
    log_info "Starting compilation for all target platforms..."

    for platform_config in "${PLATFORMS[@]}"; do
        IFS=':' read -r platform arch method <<< "$platform_config"

        if [ "$method" = "native" ] && [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
            compile_tpch_macos_native
            compile_tpcds_macos_native
        elif [ "$method" = "cross" ] && [ "$(uname -s)" = "Darwin" ]; then
            compile_tpch_macos_cross "$arch"
            compile_tpcds_macos_cross "$arch"
        elif [ "$method" = "docker" ]; then
            compile_with_docker "$platform" "$arch" "tpc-h"
            compile_with_docker "$platform" "$arch" "tpc-ds"
        else
            log_warning "Skipping ${platform}-${arch} (method: ${method}) - not supported on current host"
        fi
    done

    log_success "All platform compilation completed!"
}

verify_all_binaries() {
    log_info "Verifying all compiled binaries..."

    local failed_verifications=0
    local total_platforms=0
    local successful_platforms=0

    for platform_config in "${PLATFORMS[@]}"; do
        IFS=':' read -r platform arch method <<< "$platform_config"
        local platform_name="${platform}-${arch}"

        local tpch_dir="$PROJECT_ROOT/_binaries/tpc-h/${platform_name}"
        if [ -d "$tpch_dir" ] && [ -f "$tpch_dir/checksums.md5" ]; then
            cd "$tpch_dir"
            if md5sum -c checksums.md5 2>/dev/null || md5 -c checksums.md5 2>/dev/null; then
                log_success "TPC-H checksum verification passed for ${platform_name}"
                ((successful_platforms++))
            else
                log_error "TPC-H checksum verification failed for ${platform_name}"
                ((failed_verifications++))
            fi
        else
            log_warning "TPC-H binaries or checksums missing for ${platform_name}"
        fi

        local tpcds_dir="$PROJECT_ROOT/_binaries/tpc-ds/${platform_name}"
        if [ -d "$tpcds_dir" ] && [ -f "$tpcds_dir/checksums.md5" ]; then
            cd "$tpcds_dir"
            if md5sum -c checksums.md5 2>/dev/null || md5 -c checksums.md5 2>/dev/null; then
                log_success "TPC-DS checksum verification passed for ${platform_name}"
                ((successful_platforms++))
            else
                log_error "TPC-DS checksum verification failed for ${platform_name}"
                ((failed_verifications++))
            fi
        else
            log_warning "TPC-DS binaries or checksums missing for ${platform_name}"
        fi

        ((total_platforms++))
    done

    if [ $failed_verifications -eq 0 ]; then
        log_success "All binary verifications passed (${successful_platforms} successful)"
        return 0
    else
        log_error "$failed_verifications binary verifications failed"
        return 1
    fi
}

display_summary() {
    log_info "=== Compilation Summary ==="

    local total_binaries=0
    local successful_binaries=0

    for platform_config in "${PLATFORMS[@]}"; do
        IFS=':' read -r platform arch method <<< "$platform_config"
        local platform_name="${platform}-${arch}"

        local tpch_dir="$PROJECT_ROOT/_binaries/tpc-h/${platform_name}"
        if [ -d "$tpch_dir" ] && [ -n "$(ls -A "$tpch_dir" 2>/dev/null)" ]; then
            local tpch_status="${GREEN}✅${NC}"
            ((successful_binaries++))
        else
            local tpch_status="${RED}❌${NC}"
        fi

        local tpcds_dir="$PROJECT_ROOT/_binaries/tpc-ds/${platform_name}"
        if [ -d "$tpcds_dir" ] && [ -n "$(ls -A "$tpcds_dir" 2>/dev/null)" ]; then
            local tpcds_status="${GREEN}✅${NC}"
            ((successful_binaries++))
        else
            local tpcds_status="${RED}❌${NC}"
        fi

        printf "  %-20s TPC-H: %b  TPC-DS: %b\n" "$platform_name" "$tpch_status" "$tpcds_status"
        ((total_binaries += 2))
    done

    echo ""
    log_success "Binary compilation completed! ${successful_binaries}/${total_binaries} binaries compiled successfully"
    log_info "Binaries stored in _binaries/ directory structure"
    echo ""
    log_info "Key fixes applied:"
    log_info "  • GCC 9 used for TPC-DS compatibility"
    log_info "  • MAXINT properly defined with limits.h"
    log_info "  • -fcommon flag added for multiple definition issues"
    log_info "  • Native compilation on macOS ARM64"
    log_info "  • Docker-based cross-compilation for Linux platforms"
}

main() {
    local mode="${1:-}"

    if [[ "$mode" == "--native" ]]; then
        mode="--native-only"
    fi

    if [[ "$mode" == "--native-only" ]]; then
        log_info "Starting native-only TPC binary compilation..."
        cd "$PROJECT_ROOT"
        compile_native_only
        log_success "Build process completed successfully!"
        return
    fi

    log_info "Starting TPC binary compilation with TPC-DS fixes..."

    cd "$PROJECT_ROOT"

    if [ -z "$CONTAINER_ENGINE" ]; then
        log_error "No container engine found (looked for: container, docker, podman, mocker)"
        log_info "Install one, set BENCHBOX_CONTAINER_ENGINE=<engine>, or run with"
        log_info "--native to compile for the current platform only (no engine needed)"
        exit 1
    fi
    log_info "Using container engine: ${CONTAINER_ENGINE}"

    compile_all_platforms
    verify_all_binaries
    display_summary

    log_success "Build process completed successfully with TPC-DS compilation fixes applied!"
}

main "$@"
