#!/bin/bash

# Controlla se è stato passato almeno un parametro
if [ -z "$1" ]; then
    echo "Error: Devi specificare il nome dell'environment."
    echo "Uso: $0 <nome-environment> o all"
    exit 1
fi

# Assegna il parametro a una variabile per chiarezza
LAB_NAME=$1

if [ "$LAB_NAME" = "all" ]; then
    echo "Remove all the labs..."
    # Rimuove tutto il contenuto dentro labs, ma mantiene la cartella 'labs' stessa
    rm -rf ./labs/*
elif [ -d "./labs/$LAB_NAME" ]; then
    echo "Remove lab: $LAB_NAME..."
    # Rimuove in modo sicuro senza fare il 'cd'
    rm -rf "./labs/$LAB_NAME"
else
    echo "Error: directory does not exist or command not right"
    echo "Please use $0 <name_lab> or all"
    exit 1
fi