class Indenter:
    def __init__(self, level):
        self.level = level

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        pass

    def format_text(self, text):
        return "    " * self.level + text + "\n"
