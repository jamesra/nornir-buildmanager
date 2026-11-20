'''

Exceptions raised by pipeline functions which could possibly by addressed by end-user intervention

'''


class NornirUserException(Exception):
    '''An exception which should be displayed to the user to enable a specific user intervention'''

    def __init__(self, message, **kwargs):
        super(NornirUserException, self).__init__(**kwargs)

        self.message = message

    def __str__(self):
        return self.message

class NornirMissingDependencyException(Exception):
    '''A dependency required for the pipeline is missing.  This halts execution and displays the message the user'''

    def __init__(self, message, **kwargs):
        super(NornirMissingDependencyException, self).__init__(**kwargs)

        self.message = message

    def __str__(self):
        return self.message

class NornirRethrownException(Exception):
    """
    An exception for rethrowing an exception where output for the original exception already been provided
    and we do not want it rehandled by a recursive try/except block.
    Ensure context is preserved by using this syntax:
    raise NornirRethrownException from e
    """
    def __init__(self, **kwargs):
        super(NornirRethrownException, self).__init__(**kwargs)


class MissingMaskError(NornirUserException):
    """A slice-to-slice registration needs the Assemble mask of a section and it is missing.

    Every .stos registration runs with the masks Assemble writes (James, 2026-11-20), so a
    missing or unbuildable mask stops the stage instead of letting the pair run unmasked.
    """
