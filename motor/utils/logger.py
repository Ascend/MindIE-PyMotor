import logging


def get_logger(name: str = __name__):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter('%(levelname)s  %(asctime)s  [%(filename)s:%(lineno)d]  %(message)s')
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger