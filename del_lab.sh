#!/bin/bash

# Check whether at least one parameter was provided.
if [ -z "$1" ]; then
    echo "Error: you must specify an environment name."
    echo "Usage: $0 <environment-name> or all"
    exit 1
fi

# Lab directory path.
LAB_NAME=$1

if [ "$LAB_NAME" = "all" ]; then
    echo "Remove all the labs..."
    # Remove the contents of labs while preserving the labs directory itself.
    sudo rm -rf ./labs/*
elif [ -d "$LAB_NAME" ]; then
    echo "Remove lab: $LAB_NAME..."
    # Rimuove in modo sicuro
    sudo rm -rf "$LAB_NAME"
else
    echo "Error: directory does not exist or command not right"
    echo "Please use $0 <name_lab> or all"
    exit 1
fi
