#!/bin/bash

# Check if at least one parameter was provided
if [ -z "$1" ]; then
    echo "Error: You must specify the name of the environment."
    echo "Usage: $0 <environment-name>"
    exit 1
fi

# Define the laboratory path
LAB_PATH="$1"

# Check if the laboratory directory actually exists before starting Kathara
if [ -d "$LAB_PATH" ]; then
    echo "Starting Kathara environment: $ENV_NAME..."
    cd "$LAB_PATH" && kathara lstart
else
    echo "Error: The directory '$LAB_PATH' does not exist."
    exit 1
fi