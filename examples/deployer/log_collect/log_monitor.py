# Copyright Huawei Technologies Co., Ltd. 2026. All rights reserved.
from datetime import datetime, timezone
from typing import List, Optional
import configparser
import logging
import logging.handlers
import os
import subprocess
import threading
import time


STOP_FILE = "stop_log"

# Configuration parameters, Configured in the 'log_config.ini' file
# The log file size is configured in bytes.
# For ease of reading, you are advised to set the log file size to no more than 100 MB 
# and the number of backup log files to no more than 1000.
g_name_space = "mindie-motor"
g_target_log = "./log/"
g_max_log_size = 10000000 
g_backup_count = 10


console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))

logger_monitor = logging.getLogger("logger_monitor")
logger_monitor.setLevel(logging.INFO)
logger_monitor.handlers.clear()
logger_monitor.addHandler(console_handler)


def log_i(msg: str) -> None:
    logger_monitor.info(msg)


def log_e(msg: str) -> None:
    logger_monitor.error(msg)


class LogMonitor:
    def __init__(self):
        self.encode_type = "utf-8"
        self.cmd_kubectl = "/usr/bin/kubectl"
        self.cmd_grep = "/usr/bin/grep"
        self.cmd_awk = "/usr/bin/awk"
        self.thread_name = "thread-log-"

        self.threads = []
        self.exit_flag = threading.Event()

    def setup_rotating_logger(self, pod_name: str, log_file: str) -> Optional[logging.Logger]:
        """
        Configure a logger with a rotation function
        Args:
        log_file: Path to the log file
        Returns:
        The configured logger object or None (if creation fails)
        """
        # Create the log directory (if not existing).
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                log_i(f"Unable to create log directory {log_dir}: {e}")
                return None
        
        # Creating a Logger
        logger = logging.getLogger(pod_name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        
        # Create a RotatingFileHandler to implement log rotation.
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=g_max_log_size,
            backupCount=g_backup_count,
            encoding=self.encode_type
        )
        
        # Setting the log format (only the original log content is retained)
        formatter = logging.Formatter('%(message)s')
        handler.setFormatter(formatter)
        
        # Add a processor to the logger
        logger.addHandler(handler)
        
        return logger

    def check_pod_is_running(self, pod_name: str) -> bool:
        """
        Check if the specified pod is in the "Running" state.
        :param pod_name: Name of the pod to check
        """
        kubectl_cmd = subprocess.Popen(
            [self.cmd_kubectl, 'get', 'pods', '-A', '-o', 'wide'],
            stdout=subprocess.PIPE
        )
        grep_namespace_cmd = subprocess.Popen(
            [self.cmd_grep, g_name_space],
            stdin=kubectl_cmd.stdout,
            stdout=subprocess.PIPE
        )
        grep_name_cmd = subprocess.Popen(
            [self.cmd_grep, pod_name],
            stdin=grep_namespace_cmd.stdout,
            stdout=subprocess.PIPE
        )
        awk_cmd = subprocess.Popen(
            [self.cmd_awk, '{print $4}'],
            stdin=grep_name_cmd.stdout,
            stdout=subprocess.PIPE
        )
        output, _ = awk_cmd.communicate()
        lines = output.decode(self.encode_type).strip().splitlines()
        if len(lines) == 0:
            raise Exception("Pod not found Exception")
        if len(lines) == 1:
            if lines[0] == "Running":
                return True
        return False

    def shell_get_pod(self) -> Optional[List[str]]:
        """
        Run the kubectl command to obtain the pod list.
        Returns:
        A list of pod names or None (if an error occurs)
        """
        try:
            kubectl_cmd = subprocess.Popen(
                [self.cmd_kubectl, 'get', 'pods', '-A', '-o', 'wide'],
                stdout=subprocess.PIPE
            )
            grep_cmd = subprocess.Popen(
                [self.cmd_grep, g_name_space],
                stdin=kubectl_cmd.stdout,
                stdout=subprocess.PIPE
            )
            awk_cmd = subprocess.Popen(
                [self.cmd_awk, '{print $2}'],
                stdin=grep_cmd.stdout,
                stdout=subprocess.PIPE
            )
            output, _ = awk_cmd.communicate()
            return output.decode(self.encode_type).strip().splitlines()
        except Exception as e:
            log_e(f"shell_get_pod Exception: {e}")
            return None

    def shell_pull_log(self, pod_name: str, file_path: str, interval: float = 0.2) -> bool:
        """
        Execute the kubectl command to obtain the log.
        """
        b_write_flag = False

        logger = self.setup_rotating_logger(pod_name, file_path)
        if logger is None:
            return b_write_flag

        process = None
        try:
            process = subprocess.Popen(
                [self.cmd_kubectl, 'logs', '-f', '-n', g_name_space, pod_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            size = g_max_log_size / 1024 / 1024
            log_i(f"{pod_name} :logs save to: {file_path} (max {size:.1f}MB, Keep {g_backup_count} backups)")
            
            # Reads the output in real time and writes it to the log file.
            while not self.exit_flag.is_set():
                line = process.stdout.readline()
                if not line:
                    # Check whether the cmd command exits.
                    if process.poll() is not None:
                        log_i(f"{pod_name} : cmd has exited.")
                        break
                    time.sleep(interval)
                    continue
                b_write_flag = True
                # Remove newlines and write to log.
                log_line = line.rstrip('\n')
                logger.info(log_line)
        except Exception as e:
            log_e(f"{pod_name} :Exception: {e}")
        finally:
            # Ensure the child process is terminated
            if process and process.poll() is None:
                process.terminate()
            log_i(f"{pod_name} :The thread has exited.")
        return b_write_flag

    def pull_log_and_save(self, pod_name: str, interval: float = 3) -> None:
        """
        Collect logs from a specified pod and save them to a file.
        :param pod_name: Name of the pod to collect logs from
        """
        index = 0
        try:
            while not self.exit_flag.is_set():
                if not self.check_pod_is_running(pod_name):
                    log_i(f"{pod_name} :The pod is not in the 'Running' state, waiting...")
                    time.sleep(interval)
                    continue
                file_path = os.path.join(g_target_log, f"{pod_name}_{index}.log")
                if self.shell_pull_log(pod_name, file_path):
                    index += 1
        except Exception as e:
            log_e(f"{pod_name} :Exception: {e}")

    def monitor_stop(self, file_path: str, interval: float = 1) -> None:
        """
        Periodically check whether the specified file exists in the directory; if it exists, exit the program.
        :param file_path: Path of the file to be searched
        :param interval: Check interval (in seconds)
        """
        while True:
            # 1、Check whether the stop file exists.
            if os.path.exists(file_path):
                log_i(f"The file {file_path} exists, so the program will exit.")
                self.exit_flag.set()
                break
            # 2、Check whether there are any alive threads
            flag = False
            for thread in self.threads:
                if thread.is_alive():
                    flag = True
                    break
            if not flag:
                log_i("All thread have terminated abnormally, so the program will exit.")
                self.exit_flag.set()
                break
        
            time.sleep(interval)

    def start_log_thread(self) -> bool:
        # 1、Get pod information.
        list_line = self.shell_get_pod()
        if not list_line:
            log_i("No pod information available, exiting program.")
            return False

        # 2、Get newly created pods.
        thread_names = [
            thread.name 
            for thread in self.threads
            if thread.name.startswith(self.thread_name)
        ]

        list_line = [
            pod_name 
            for pod_name in list_line
            if f"{self.thread_name}{pod_name}" not in thread_names
        ]
        if len(list_line) == 0:
            return True

        # 3、Start the thread for collecting logs.
        for pod_name in list_line:
            log_i(f"pod_name: {pod_name}")
            thread = threading.Thread(
                target=self.pull_log_and_save,
                args=(pod_name,),
                name=f"{self.thread_name}{pod_name}",
                daemon=True
            )
            self.threads.append(thread)
            thread.start()
        log_i(f" {len(list_line)} threads have been started.")
        return True
           

    def do(self, interval: float = 5):
        # 1、Start collect log.
        if not self.start_log_thread():
            return

        # 2、Check whether the stop file for detecting exit exists.
        threading.Thread(
            target=self.monitor_stop,
            args=(STOP_FILE,),
            name="thread-monitor",
            daemon=True
        ).start()

        # 3、Start collect log loop.
        while not self.exit_flag.is_set():
            if not self.start_log_thread():
                break
            time.sleep(interval)

        # 4、Exit gracefully.
        for thread in self.threads:
            thread.join()
        log_i("All tasks have been terminated")


def read_config(config_file: str) -> None:
    """
    Read and apply the contents of the configuration file.
    :param config_file: Path to the configuration file
    """
    global g_name_space, g_target_log, g_max_log_size, g_backup_count
    
    config = configparser.ConfigParser()
    if not config.read(config_file):
        log_i(f"The configuration file {config_file} does not exist or cannot be read.")
        return
    
    section = 'LogSetting'

    for section in config.sections():
        log_i(f"[{section}]")
        for key, value in config[section].items():
            log_i(f"{key} = {value}")

    if section in config:
        g_name_space = config[section].get('name_space', g_name_space)
        g_target_log = config[section].get('out_path', g_target_log)
        g_max_log_size = config[section].getint('max_log_size', g_max_log_size)
        g_backup_count = config[section].getint('backup_count', g_backup_count)

        g_target_log = os.path.join(g_target_log, datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S'))
        log_i(f"Read configuration: [{section}] succeeded. output : {g_target_log}")
    else:
        log_e(f"The [{section}] part is missing in the configuration file; default settings will be used.")


if __name__ == "__main__":
    log_i(f"Use the command [touch {os.getcwd()}/{STOP_FILE}] to stop the background process.")
    read_config("log_config.ini")
    LogMonitor().do()
