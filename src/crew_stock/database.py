"""Small PyMySQL execution interface; queries use native MySQL parameters."""


class Connection:
    def __init__(self, raw):
        self.raw = raw
        self.cursors = []

    def execute(self, sql, parameters=None):
        cursor = self.raw.cursor()
        self.cursors.append(cursor)
        cursor.execute(sql, parameters)
        return cursor

    def executemany(self, sql, parameters):
        cursor = self.raw.cursor()
        self.cursors.append(cursor)
        if parameters:
            cursor.executemany(sql, parameters)
        return cursor

    def close(self):
        for cursor in self.cursors:
            cursor.close()
        self.raw.close()
