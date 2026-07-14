#!/bin/bash

set -u

# Check if at least one parameter was provided
if [ $# -lt 1 ]; then
    echo "Error: You must specify the name of the environment to stop."
    echo "Usage: $0 <environment-name>"
    exit 1
fi


# Define the laboratory path
LAB_PATH="$1"
LAB_NAME="$(basename "$LAB_PATH")"

# Check if the directory exists before running the command
if [ -d "$LAB_PATH" ]; then
    echo "Stopping and cleaning up Kathara environment: $LAB_NAME..."
    cd "$LAB_PATH" || exit 1
    kathara lclean
else
    echo "Error: The directory '$LAB_PATH' does not exist."
    exit 1
fi
