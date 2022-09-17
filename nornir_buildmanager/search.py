def IsMatch(in_string: str, RegExStr: str, CaseSensitive: bool = False) -> bool:
    """
    :returns: true if the in_string matches the regular expression string
    """
    if RegExStr == '*':
        return True

    flags = 0
    if not CaseSensitive:
        flags = re.IGNORECASE

    match = re.match(RegExStr, in_string, flags)
    if match is not None:
        return True

    return False