def lowpriority():
    """ Set the priority of the process to below-normal.
        Shamelessly copied from:
        http://stackoverflow.com/questions/1023038/change-process-priority-in-python-cross-platform"""

    import sys
    import os
    import nornir_shared.prettyoutput as prettyoutput

    try:
        sys.getwindowsversion()
    except:
        isWindows = False
    else:
        isWindows = True

    if isWindows:
        try:

            # Based on:
            #   "Recipe 496767: Set Process Priority In Windows" on ActiveState
            #   http://code.activestate.com/recipes/496767/
            import win32api, win32process, win32con
            pid = os.getpid()
            handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, True, pid);
            win32process.SetPriorityClass(handle, win32process.BELOW_NORMAL_PRIORITY_CLASS);
        except:
            prettyoutput.Log("Could not lower process priority, missing Win32 extensions for python?");
            pass;
    else:
        import os
        os.nice(1)
