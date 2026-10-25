try:
    import pymysql
    from pymysql.cursors import DictCursor as PyMySQLDictCursor

    PYMYSQL_AVAILABLE = True
except ImportError:
    pymysql = None
    PyMySQLDictCursor = None
    PYMYSQL_AVAILABLE = False

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

STARROCKS_AVAILABLE = PYMYSQL_AVAILABLE

__all__ = [
    "pymysql",
    "PyMySQLDictCursor",
    "requests",
    "PYMYSQL_AVAILABLE",
    "REQUESTS_AVAILABLE",
    "STARROCKS_AVAILABLE",
]
