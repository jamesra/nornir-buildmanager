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

if __name__ == '__main__':
    pass
