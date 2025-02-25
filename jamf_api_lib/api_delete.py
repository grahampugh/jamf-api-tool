#!/usr/bin/env python3

"""Functions that help delete objects using the API"""

from time import sleep

from . import curl, api_objects, api_get  # pylint: disable=no-name-in-module


def delete_api_object(jamf_url, object_type, obj_id, token, verbosity):
    """deletes an API object by obtained or set id"""
    url = jamf_url + "/" + api_objects.api_endpoints(object_type) + f"/id/{obj_id}"

    count = 0
    while True:
        count += 1
        if verbosity > 1:
            print(f"{object_type} delete attempt {count}")
        request_type = "DELETE"
        r = curl.request(request_type, url, token, verbosity)
        # check HTTP response
        if curl.status_check(r, object_type, obj_id, request_type) == "break":
            break
        if count > 5:
            print(f"WARNING: {object_type} delete did not succeed after 5 attempts")
            print(f"\nHTTP POST Response Code: {r.status_code}")
            break
        else:
            print(f"\nHTTP POST Response Code: {r.status_code}")
            print(f"Waiting {count}s to try again...")
            sleep(count)
    if verbosity > 1:
        api_get.get_headers(r)
    return r.status_code


def delete_uapi_object(jamf_url, object_type, obj_id, token, verbosity):
    """deletes an API object by obtained or set id"""
    url = jamf_url + "/" + api_objects.api_endpoints(object_type) + f"/{obj_id}"

    count = 0
    while True:
        count += 1
        if verbosity > 1:
            print(f"{object_type} delete attempt {count}")
        request_type = "DELETE"
        r = curl.request(request_type, url, token, verbosity)
        # check HTTP response
        if curl.status_check(r, object_type, obj_id, request_type) == "break":
            break
        if count > 5:
            print(f"WARNING: {object_type} delete did not succeed after 5 attempts")
            print(f"\nHTTP POST Response Code: {r.status_code}")
            break
        else:
            print(f"\nHTTP POST Response Code: {r.status_code}")
            print(f"Waiting {count}s to try again...")
            sleep(count)
    if verbosity > 1:
        api_get.get_headers(r)
    return r.status_code
