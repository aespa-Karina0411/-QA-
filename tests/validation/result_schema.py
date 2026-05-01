"""统一验证结果结构"""


class ValidationResult:
    def __init__(self, name):
        self.name = name
        self.metrics = {}
        self.passed = True
        self.errors = []

    def add_metric(self, key, value):
        self.metrics[key] = value

    def fail(self, reason):
        self.passed = False
        self.errors.append(reason)
