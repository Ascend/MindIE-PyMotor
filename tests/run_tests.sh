#!/bin/bash

# Function: Display help information
show_help() {
    echo "Usage: $0 [options] [test_path]"
    echo "Options:"
    echo "  -h, --help        Show this help message"
    echo "  -v, --verbose     Show detailed test output"
    echo "  -s                Show test output content"
    echo "  -x                Stop immediately on first failure"
    echo "  --cov             Enable code coverage statistics"
    echo "  --cov-report=    Specify coverage report format (term/html/xml)"
    echo "  --exclude=        Exclude files/directories from coverage (supports wildcards)"
    echo "  -k EXPRESSION     Only run tests matching the expression"
    echo ""
    echo "Default exclusion rules:"
    echo "  - Test files: */tests/*"
    echo "  - Cache files: */__pycache__/*"
    echo "  - gRPC generated files: */cluster_grpc/*_pb2*.py, */cluster_grpc/*_grpc.py"
    echo ""
    echo "Examples:"
    echo "  $0 --cov --cov-report=html tests/controller/"
    echo "  $0 --cov --cov-report=html tests/coordinator/"
    echo "  $0 -v -k 'test_register'"
    echo "  $0 --cov --exclude='motor/config/*' --exclude='motor/utils/logger.py'"
}

# Ensure we're in the project root directory
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Clean previous coverage data
rm -f .coverage
rm -rf htmlcov

# Initialize variables
PYTEST_ARGS=""
COVERAGE_ENABLED=false
COVERAGE_REPORT="term"
COVERAGE_EXCLUDE=()

# Default excluded files/directories (generated files, test files, etc.)
DEFAULT_EXCLUDES=(
    "*/tests/*"
    "*/__pycache__/*"
    "*/cluster_grpc/*_pb2*.py"
    "*/cluster_grpc/*_grpc.py"
)

# Set PYTHONPATH
export PYTHONPATH="$ROOT_DIR:$PYTHONPATH"
export PYTHONPATH="$ROOT_DIR/motor:$PYTHONPATH"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        --cov)
            COVERAGE_ENABLED=true
            shift
            ;;
        --cov-report=*)
            COVERAGE_REPORT="${1#*=}"
            shift
            ;;
        --exclude=*)
            COVERAGE_EXCLUDE+=("${1#*=}")
            shift
            ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $1"
            shift
            ;;
    esac
done

# Check if required packages are installed
check_dependencies() {
    echo "Checking test dependencies..."
    
    # Check pytest related packages
    python3 -c "import pytest" 2>/dev/null || { echo "Installing pytest..."; pip install pytest; }
    python3 -c "import pytest_cov" 2>/dev/null || { echo "Installing pytest-cov..."; pip install pytest-cov; }
    python3 -c "import pytest_asyncio" 2>/dev/null || { echo "Installing pytest-asyncio..."; pip install pytest-asyncio; }
    
    # Check project core dependencies
    echo "Checking project core dependencies..."
    python3 -c "import psutil" 2>/dev/null || { echo "Installing psutil..."; pip install psutil>=5.9.8; }
    python3 -c "import fastapi" 2>/dev/null || { echo "Installing fastapi..."; pip install fastapi>=0.68.0; }
    python3 -c "import uvicorn" 2>/dev/null || { echo "Installing uvicorn..."; pip install "uvicorn[standard]>=0.15.0"; }
    python3 -c "import grpc" 2>/dev/null || { echo "Installing grpcio..."; pip install grpcio>=1.40.0; }
    python3 -c "import grpc_tools" 2>/dev/null || { echo "Installing grpcio-tools..."; pip install grpcio-tools>=1.40.0; }
    python3 -c "import pydantic" 2>/dev/null || { echo "Installing pydantic..."; pip install pydantic>=1.8.0; }
    python3 -c "from OpenSSL import crypto" 2>/dev/null || { echo "Installing pyOpenSSL..."; pip install pyOpenSSL>=21.0.0; }
    
    # Check HTTP client libraries
    echo "Checking HTTP client dependencies..."
    python3 -c "import requests" 2>/dev/null || { echo "Installing requests..."; pip install requests>=2.25.0; }
    python3 -c "import httpx" 2>/dev/null || { echo "Installing httpx..."; pip install httpx>=0.24.0; }
    
    # Check other potentially needed test dependencies
    python3 -c "import asyncio" 2>/dev/null || { echo "asyncio is not available, this may affect async tests"; }
    python3 -c "import tempfile" 2>/dev/null || { echo "tempfile is not available, this may affect temporary file tests"; }
    
    echo "Dependency check completed"
}

# Ensure dependency packages are installed
check_dependencies

# Generate protobuf files if needed
generate_proto_files() {
    echo "Checking and generating protobuf files..."
    if [ -f "$ROOT_DIR/scripts/generate_proto.sh" ]; then
        "$ROOT_DIR/scripts/generate_proto.sh"
    else
        echo "Warning: generate_proto.sh not found. Skipping protobuf generation."
        echo "If you encounter import errors, please run: scripts/generate_proto.sh"
    fi
}

generate_proto_files

# Build pytest command
CMD="pytest"

# Enable color output (force color on since we're using tee which may interfere with auto-detection)
# Check if color option is not already specified in PYTEST_ARGS
if ! echo " $PYTEST_ARGS " | grep -qE " (--color=|--color )"; then
    CMD="$CMD --color=yes"
fi

# If coverage statistics are enabled
if [ "$COVERAGE_ENABLED" = true ]; then
    # Create temporary .coveragerc configuration file
    COVERAGERC_FILE=".coveragerc.tmp"
    cat > "$COVERAGERC_FILE" << EOF
[run]
source = motor
omit = 
EOF
    
    # Add exclusion file configuration (default exclusion rules + user-specified rules)
    for exclude_pattern in "${DEFAULT_EXCLUDES[@]}"; do
        echo "    $exclude_pattern" >> "$COVERAGERC_FILE"
    done
    
    if [ ${#COVERAGE_EXCLUDE[@]} -gt 0 ]; then
        for exclude_pattern in "${COVERAGE_EXCLUDE[@]}"; do
            echo "    $exclude_pattern" >> "$COVERAGERC_FILE"
        done
    fi
    
    # Specify source code path and test path
    CMD="$CMD --cov=motor --cov-report=$COVERAGE_REPORT --cov-config=$COVERAGERC_FILE"
fi

# Add other pytest arguments
if [ ! -z "$PYTEST_ARGS" ]; then
    CMD="$CMD $PYTEST_ARGS"
else
    # If no test path is specified, run all tests by default
    CMD="$CMD tests/"
fi

# Function: Parse and display test result summary
show_test_summary() {
    local exit_code=$1
    local output_file=$2
    
    echo ""
    echo "=========================================="
    echo "Test Result Summary"
    echo "=========================================="
    
    # Extract test statistics from output
    if [ -f "$output_file" ]; then
        # Extract test statistics line (usually contains passed, failed, skipped, warnings, etc.)
        local stats=$(grep -E "(passed|failed|error|skipped|warnings|warnings summary)" "$output_file" | tail -1)
        
        # Extract statistics from the last few lines of pytest output (pytest usually shows summary in the last line)
        # Look for the pytest summary line which typically looks like: "606 passed, 5 warnings in 10.43s"
        local last_lines=$(tail -10 "$output_file")
        
        # Find the pytest summary line (contains "passed" or "failed" and ends with time)
        local summary_line=$(echo "$last_lines" | grep -E "[0-9]+ (passed|failed|error)" | grep -E "in [0-9]+\.[0-9]+s" | tail -1)
        
        # If summary line not found in last 10 lines, search more broadly
        if [ -z "$summary_line" ]; then
            summary_line=$(grep -E "[0-9]+ (passed|failed|error)" "$output_file" | grep -E "in [0-9]+\.[0-9]+s" | tail -1)
        fi
        
        # Extract counts for various test states from the summary line
        if [ -n "$summary_line" ]; then
            passed=$(echo "$summary_line" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+" | head -1 || echo "0")
            failed=$(echo "$summary_line" | grep -oE "[0-9]+ failed" | grep -oE "[0-9]+" | head -1 || echo "0")
            errors=$(echo "$summary_line" | grep -oE "[0-9]+ error" | grep -oE "[0-9]+" | head -1 || echo "0")
            skipped=$(echo "$summary_line" | grep -oE "[0-9]+ skipped" | grep -oE "[0-9]+" | head -1 || echo "0")
            # Extract warnings (supports both "warning" and "warnings")
            warnings=$(echo "$summary_line" | grep -oE "[0-9]+ warnings?" | grep -oE "[0-9]+" | head -1 || echo "0")
        else
            # Fallback: try to extract from last lines without summary line format
            passed=$(echo "$last_lines" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+" | head -1 || echo "0")
            failed=$(echo "$last_lines" | grep -oE "[0-9]+ failed" | grep -oE "[0-9]+" | head -1 || echo "0")
            errors=$(echo "$last_lines" | grep -oE "[0-9]+ error" | grep -oE "[0-9]+" | head -1 || echo "0")
            skipped=$(echo "$last_lines" | grep -oE "[0-9]+ skipped" | grep -oE "[0-9]+" | head -1 || echo "0")
            warnings=$(echo "$last_lines" | grep -oE "[0-9]+ warnings?" | grep -oE "[0-9]+" | head -1 || echo "0")
        fi
        
        # If still not found, try searching the entire file
        if [ -z "$passed" ] || [ "$passed" = "0" ]; then
            passed=$(grep -oE "[0-9]+ passed" "$output_file" | grep -oE "[0-9]+" | head -1 || echo "0")
            failed=$(grep -oE "[0-9]+ failed" "$output_file" | grep -oE "[0-9]+" | head -1 || echo "0")
            errors=$(grep -oE "[0-9]+ error" "$output_file" | grep -oE "[0-9]+" | head -1 || echo "0")
            skipped=$(grep -oE "[0-9]+ skipped" "$output_file" | grep -oE "[0-9]+" | head -1 || echo "0")
            # Also search for warnings in the entire file
            if [ -z "$warnings" ] || [ "$warnings" = "0" ]; then
                warnings=$(grep -oE "[0-9]+ warnings?" "$output_file" | grep -oE "[0-9]+" | head -1 || echo "0")
            fi
        fi
        
        # Check if there are warnings (from summary line)
        local has_warnings=false
        if [ -n "$warnings" ] && [ "$warnings" != "0" ]; then
            has_warnings=true
        fi
        
        # Display statistics
        echo "Test Status:"
        local has_stats=false
        
        if [ -n "$passed" ] && [ "$passed" != "0" ]; then
            echo "  ✓ Passed: $passed"
            has_stats=true
        fi
        if [ -n "$failed" ] && [ "$failed" != "0" ]; then
            echo "  ✗ Failed: $failed"
            has_stats=true
        fi
        if [ -n "$errors" ] && [ "$errors" != "0" ]; then
            echo "  ✗ Errors: $errors"
            has_stats=true
        fi
        if [ -n "$skipped" ] && [ "$skipped" != "0" ]; then
            echo "  ⊘ Skipped: $skipped"
            has_stats=true
        fi
        if [ "$has_warnings" = true ]; then
            echo "  ⚠ Warnings: $warnings"
            has_stats=true
        fi
        
        # If no statistics were extracted, show a hint
        if [ "$has_stats" = false ]; then
            echo "  (Unable to extract detailed statistics from output)"
        fi
        
        # Display overall status
        echo ""
        echo "Overall Status:"
        if [ $exit_code -eq 0 ]; then
            if [ "$has_warnings" = true ]; then
                echo "  ⚠ All tests passed, but with warnings"
            else
                echo "  ✓ All tests passed"
            fi
        else
            case $exit_code in
                1)
                    echo "  ✗ Tests failed"
                    ;;
                2)
                    echo "  ✗ Tests interrupted"
                    ;;
                3)
                    echo "  ✗ Internal error"
                    ;;
                4)
                    echo "  ✗ pytest usage error"
                    ;;
                5)
                    echo "  ✗ No tests collected"
                    ;;
                *)
                    echo "  ✗ Unknown error (exit code: $exit_code)"
                    ;;
            esac
        fi
        
        # If there are failures or errors, show hint for failure details location
        if [ "$failed" != "0" ] || [ "$errors" != "0" ]; then
            echo ""
            echo "Hint: Check the output above for detailed information about failed tests"
        fi
        
        # If there are warnings, show hint for warning details
        if [ "$has_warnings" = true ]; then
            echo ""
            echo "Hint: Check the output above for detailed warning information"
        fi
    else
        # If no output file, judge based on exit code
        echo "Test Status:"
        if [ $exit_code -eq 0 ]; then
            echo "  ✓ All tests passed"
        else
            echo "  ✗ Tests did not pass completely (exit code: $exit_code)"
        fi
    fi
    
    echo "=========================================="
    echo ""
}

# Execute test command
echo "Executing command: $CMD"

# Create temporary file to capture output
OUTPUT_FILE=$(mktemp)
trap "rm -f $OUTPUT_FILE" EXIT

# Ensure terminal width is detected correctly for progress alignment
# Get terminal width, default to 80 if not available
TERM_WIDTH=${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}
export COLUMNS=$TERM_WIDTH

# Execute tests and capture output and exit code
$CMD 2>&1 | tee "$OUTPUT_FILE"
TEST_EXIT_CODE=${PIPESTATUS[0]}

# Display test result summary
show_test_summary $TEST_EXIT_CODE "$OUTPUT_FILE"

# Clean up temporary files
if [ -f ".coveragerc.tmp" ]; then
    rm -f ".coveragerc.tmp"
fi

# Exit based on test results
exit $TEST_EXIT_CODE