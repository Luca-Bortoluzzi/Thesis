#!/bin/bash

set -u

# Check if at least one parameter was provided
if [ $# -lt 1 ]; then
    echo "Error: You must specify the name of the environment."
    echo "Usage: $0 <environment-name>"
    exit 1
fi

# Define the laboratory path
LAB_PATH="$1"
LAB_NAME="$(basename "$LAB_PATH")"

# Check if the laboratory directory actually exists before starting Kathara
if [ -d "$LAB_PATH" ]; then
    echo "Starting Kathara environment: $LAB_NAME..."
    cd "$LAB_PATH" || exit 1
    kathara lstart
else
    echo "Error: The directory '$LAB_PATH' does not exist."
    exit 1
fi
