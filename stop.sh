#!/bin/bash

# Check if at least one parameter was provided
if [ -z "$1" ]; then
    echo "Error: You must specify the name of the environment to stop."
    echo "Usage: $0 <environment-name>"
    exit 1
fi

# Assign the parameter to a variable
ENV_NAME=$1

# Define the laboratory path
LAB_PATH="./labs/$ENV_NAME"

# Check if the directory exists before running the command
if [ -d "$LAB_PATH" ]; then
    echo "Stopping and cleaning up Kathara environment: $ENV_NAME..."
    cd "$LAB_PATH" && kathara lclean
else
    echo "Error: The directory '$LAB_PATH' does not exist."
    exit 1
fi