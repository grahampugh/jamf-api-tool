#!/usr/bin/env python3

"""Functions that help perform curl requests"""


import json
import os
import re
import subprocess
import uuid
from shutil import rmtree
from collections import namedtuple


def request(method, url, auth, verbosity, data="", additional_headers="", xml=False):
    """
    build a curl command based on method (GET, PUT, POST, DELETE)
    If the URL contains 'uapi' then token should be passed to the auth variable,
    otherwise the enc_creds variable should be passed to the auth variable
    """
    tmp_dir = make_tmp_dir()
    headers_file = os.path.join(tmp_dir, "curl_headers_from_jamf_upload.txt")
    output_file = os.path.join(tmp_dir, "curl_output_from_jamf_upload.txt")
    cookie_jar = os.path.join(tmp_dir, "curl_cookies_from_jamf_upload.txt")

    # build the curl command
    curl_cmd = [
        "/usr/bin/curl",
        "--silent",
        "--show-error",
        "-X",
        method,
        "-D",
        headers_file,
        "--output",
        output_file,
        url,
    ]

    # the authorisation requires a token (except Slack)
    if "/token" not in url:
        curl_cmd.extend(["--header", f"authorization: Bearer {auth}"])
    elif "slack" not in url:
        curl_cmd.extend(["--header", f"authorization: Basic {auth}"])

    # set either Accept or Content-Type depending on method
    if method == "GET" or method == "DELETE":
        if xml:
            curl_cmd.extend(["--header", "Accept: application/xml"])
        else:
            curl_cmd.extend(["--header", "Accept: application/json"])
    elif method == "POST" and "hooks.slack.com" in url:
        # build a Slack-centric curl command - create a webhook url on slack.com
        # and set variable to SLACK_WEBHOOK in your prefs file
        curl_cmd = [
            "/usr/bin/curl",
            "-X",
            method,
            "-H",
            "Content-type: application/json",
            "--data",
            json.dumps(data),
            url,
        ]
    # icon upload requires special method
    elif method == "POST" and "fileuploads" in url:
        curl_cmd.extend(["--header", "Content-type: multipart/form-data"])
        curl_cmd.extend(["--form", f"name=@{data}"])
    elif method == "POST" or method == "PUT":
        if data:
            curl_cmd.extend(["--upload-file", data])
        # uapi sends json, classic API must send xml
        if "uapi" in url:
            curl_cmd.extend(["--header", "Content-type: application/json"])
        else:
            curl_cmd.extend(["--header", "Content-type: application/xml"])
    else:
        print(f"WARNING: HTTP method {method} not supported")

    # write session
    try:
        with open(headers_file, "r", encoding="utf-8") as file:
            headers = file.readlines()
        existing_headers = [x.strip() for x in headers]
        for header in existing_headers:
            if "APBALANCEID" in header:
                with open(cookie_jar, "w", encoding="utf-8") as fp:
                    fp.write(header)
    except IOError:
        pass

    # look for existing session
    try:
        with open(cookie_jar, "r", encoding="utf-8") as file:
            headers = file.readlines()
        existing_headers = [x.strip() for x in headers]
        for header in existing_headers:
            if "APBALANCEID" in header:
                cookie = header.split()[1].rstrip(";")
                if verbosity > 1:
                    print(f"Existing cookie found: {cookie}")
                curl_cmd.extend(["--cookie", cookie])

    except IOError:
        if verbosity > 1:
            print("No existing cookie found - starting new session")

    # additional headers for advanced requests
    if additional_headers:
        curl_cmd.extend(additional_headers)

    # add or remove verbose mode
    if verbosity < 1:
        curl_cmd.append("-s")
    elif verbosity > 1:
        curl_cmd.append("-v")

    if verbosity > 1:
        print(f"\ncurl command:\n{curl_cmd}")
        print("(note this may omit essential quotation marks - do not copy-and-paste!")

    # now subprocess the curl command and build the r tuple which contains the
    # headers, status code and outputted data
    subprocess.check_output(curl_cmd)

    r = namedtuple(
        "r", ["headers", "status_code", "output"], defaults=(None, None, None)
    )
    try:
        with open(headers_file, "r", encoding="utf-8") as file:
            headers = file.readlines()
        r.headers = [x.strip() for x in headers]
        for header in r.headers:  # pylint: disable=not-an-iterable
            if re.match(r"HTTP/(1.1|2)", header) and "Continue" not in header:
                r.status_code = int(header.split()[1])
    except IOError:
        print(f"WARNING: {headers_file} not found")
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        with open(output_file, "rb") as file:
            if "uapi" in url:
                r.output = json.load(file)
            else:
                r.output = file.read()
    else:
        print(f"No output from request ({output_file} not found or empty)")
    return r()


def status_check(r, endpoint_type, obj_name, request_type="upload"):
    """Return a message dependent on the HTTP response"""

    if r.status_code == 200 or r.status_code == 201 or r.status_code == 204:
        print(f"{endpoint_type} '{obj_name}' {request_type.lower()} successful")
        return "break"
    elif r.status_code == 409:
        print(
            f"WARNING: {endpoint_type} {request_type.lower()} failed due to a conflict"
        )
        return "break"
    elif r.status_code == 404:
        print(f"WARNING: {endpoint_type} '{obj_name}' not found")
        return "break"
    elif r.status_code == 401:
        print(
            f"ERROR: {endpoint_type} {request_type.lower()} failed due to permissions error"
        )
        return "break"


def write_json_file(data, tmp_dir="/tmp/jamf_upload"):
    """dump some json to a temporary file"""
    make_tmp_dir(tmp_dir)
    tf = os.path.join(tmp_dir, f"jamf_upload_{str(uuid.uuid4())}.json")
    with open(tf, "w", encoding="utf-8") as fp:
        json.dump(data, fp)
    return tf


def write_temp_file(data, tmp_dir="/tmp/jamf_upload"):
    """dump some text to a temporary file"""
    make_tmp_dir(tmp_dir)
    tf = os.path.join(tmp_dir, f"jamf_upload_{str(uuid.uuid4())}.txt")
    with open(tf, "w", encoding="utf-8") as fp:
        fp.write(data)
    return tf


def make_tmp_dir(tmp_dir="/tmp/jamf_upload"):
    """make the tmp directory"""
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)
    return tmp_dir


def clear_tmp_dir(tmp_dir="/tmp/jamf_upload"):
    """remove the tmp directory"""
    if os.path.exists(tmp_dir):
        rmtree(tmp_dir)
    return tmp_dir
