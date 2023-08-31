class Config:
    def include_filename(self, *, path: str) -> bool:
        return True

    def ignore_error(self, *, row, error: str) -> bool:
        # TODO import Row type?
        return False
