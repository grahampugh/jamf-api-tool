#!/usr/bin/env python3

"""Functions that perform miscellaneous actions"""

import re
import subprocess

from xml.sax.saxutils import escape

from urllib.parse import urlparse


def substitute_assignable_keys(data, cli_custom_keys, verbosity, xml_escape=False):
    """substitutes any key in the inputted text using the %MY_KEY% nomenclature.
    Whenever %MY_KEY% is found in the provided data, it is replaced with the assigned
    value of MY_KEY. A five-times passa through is done to ensure that all keys are substituted.

    Optionally, if the xml_escape key is set, the value is escaped for XML special characters.
    This is designed primarily to account for ampersands in the substituted strings."""
    loop = 5
    while loop > 0:
        loop = loop - 1
        found_keys = re.findall(r"\%\w+\%", data)
        if not found_keys:
            break
        found_keys = [i.replace("%", "") for i in found_keys]
        for found_key in found_keys:
            if cli_custom_keys[found_key]:
                if verbosity:
                    print(
                        f"Replacing any instances of '{found_key}' with",
                        f"'{str(cli_custom_keys[found_key])}'",
                    )
                if xml_escape:
                    replacement_key = escape(cli_custom_keys[found_key])
                else:
                    replacement_key = cli_custom_keys[found_key]
                data = data.replace(f"%{found_key}%", replacement_key)
            else:
                print(
                    f"WARNING: '{found_key}' has no replacement object!",
                )
    return data


def status_check(r, endpoint_type, obj_name):
    """Return a message dependent on the HTTP response"""
    if r.status_code == 200 or r.status_code == 201:
        print(f"{endpoint_type} '{obj_name}' uploaded successfully")
        return "break"
    elif r.status_code == 409:
        print(f"WARNING: {endpoint_type} upload failed due to a conflict")
        return "break"
    elif r.status_code == 401:
        print(f"ERROR: {endpoint_type} upload failed due to permissions error")
        return "break"
    else:
        print(
            f"WARNING: {endpoint_type} '{obj_name}' upload failed with status code {r.status_code}"
        )


def confirm(prompt=None, default=False):
    """prompts for yes or no response from the user. Returns True for yes and
    False for no.

    'default' should be set to the default value assumed by the caller when
    user simply types ENTER.

    Examples:

    confirm(prompt='Create Directory?', default=True)
    Create Directory? [y]|n|q:
    True

    confirm(prompt='Create Directory?', default=False)
    Create Directory? [n]|y|q:
    False

    confirm(prompt='Create Directory?', default=False)
    Create Directory? [n]|y|q: y
    True

    Source:
    https://code.activestate.com/recipes/541096-prompt-the-user-for-confirmation/
    """

    if prompt is None:
        prompt = "Confirm"

    if default:
        prompt = f"{prompt} [y]|n|q: "
    else:
        prompt = f"{prompt} [n]|y|q: "

    while True:
        answer = input(prompt)
        if not answer:
            return default
        if answer not in ["y", "Y", "n", "N", "q", "Q"]:
            print("Please enter Y or N or Q.")
            continue
        if answer == "y" or answer == "Y":
            return True
        if answer == "n" or answer == "N":
            return False
        if answer == "q" or answer == "Q":
            print("Quitting as Q selected")
            exit()


def mount_smb(mount_share, mount_user, mount_pass, verbosity):
    """Mount distribution point."""
    mount_cmd = [
        "/usr/bin/osascript",
        "-e",
        f'mount volume "{mount_share}" as user name "{mount_user}" with password "{mount_pass}"',
    ]
    if verbosity > 1:
        print(f"Mount command:\n{mount_cmd}")

    r = subprocess.check_output(mount_cmd)
    if verbosity > 1:
        print(f"Mount command response:\n{r.decode('UTF-8')}")


def umount_smb(mount_share):
    """Unmount distribution point."""
    path = f"/Volumes{urlparse(mount_share).path}"
    cmd = ["/usr/sbin/diskutil", "unmount", path]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        print("WARNING! Unmount failed.")
