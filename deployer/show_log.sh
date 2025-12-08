#!/usr/bin/env bash

# Get the current time, formatted as YYYY-MM-DD_HH-MM-SS
time=$(date +"%Y-%m-%d_%H-%M-%S")
log_dir="./log/log_${time}"

# Creating a Log Directory
mkdir -p "$log_dir"

# Get all Pods of mindie-pymotor: namespace name node
pods=$(kubectl get pods -A -o wide | grep "mindie-pymotor" | awk '{print $1 " " $2 " " $8}')

# Check if a matching Pod is found
if [[ -z "$pods" ]]; then
    echo "No Pods for mindie-pymotor found."
    exit 1
fi

# Capture the interrupt signal and stop all child processes.
trap 'echo "Stop logging..."; pkill -P $$ || true; exit 0' INT TERM

# Process each pod in a loop, logging asynchronously.
echo "$pods" | while read -r namespace podname nodename; do
    logfile="${log_dir}/${podname}_${nodename}.log"
    echo "Logging for Pod [$podname] (Namespace: $namespace) is being recorded to $logfile"
    kubectl logs -f -n "$namespace" "$podname" > "$logfile" 2>&1 &
done

echo "Log recording completed. Logs are saved at $log_dir"
