#!/usr/bin/env python3

"""
** Jamf API Tool: List, search and clean policies and computer objects

Credentials can be supplied from the command line as arguments, or inputted, or
from an existing PLIST containing values for 
JSS_URL, API_USERNAME and API_PASSWORD,
for example an AutoPkg preferences file which has been configured for use with
JSSImporter: ~/Library/Preferences/com.github.autopkg

For usage, run jamf_api_tool.py --help
"""


import argparse
import csv
import getpass
import os
import pathlib
import sys

from datetime import datetime

from jamf_api_lib import api_connect, api_get, api_delete, curl, actions, smb_actions


class Bcolors:
    """Colours for print outs"""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[33m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def send_slack_notification(
    jamf_url,
    jamf_user,
    slack_webhook,
    api_xml_object,
    chosen_api_obj_name,
    api_obj_action,
    status_code,
):
    """Send a Slack notification"""

    slack_payload = str(
        "*jamf-api-tool.py*\n"
        f"*API {api_xml_object} {api_obj_action} action*\n"
        f"User: {jamf_user}\n"
        f"Object Name: *{chosen_api_obj_name}*\n"
        f"Instance: {jamf_url}\n"
        f"HTTP Response: {status_code}"
    )

    print(slack_payload)

    data = {"username": jamf_url, "text": slack_payload}

    url = slack_webhook
    request_type = "POST"
    curl.request(method=request_type, auth="", url=url, verbosity=1, data=data)


def handle_computers(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling computer lists"""
    if args.search and args.all:
        exit("syntax error: use either --search or --all, but not both")
    if not args.all:
        exit("syntax error: --computers requires --all as a minimum")

    recent_computers = []  # we'll need this later
    old_computers = []
    warning = []  # stores full detailed computer info
    compliant = []

    if args.all:
        # fill up computers
        obj = api_get.get_api_obj_list(jamf_url, "computer", token, verbosity)

        try:
            computers = []
            for x in obj:
                computers.append(x["id"])

        except IndexError:
            computers = "404 computers not found"

        print(f"{len(computers)} computers found on {jamf_url}")

    for x in computers:
        # load full computer info now
        print(f"...loading info for computer {x}")
        obj = api_get.get_api_obj_value_from_id(
            jamf_url, "computer", x, "", token, verbosity
        )

        macos = "unknown"
        name = "unknown"
        dep = "unknown"
        seen = "unknown"
        now = "unknown"
        difference = "unknown"

        if obj:
            # this is now computer object
            try:
                macos = obj["hardware"]["os_version"]
                name = obj["general"]["name"]
                dep = obj["general"]["management_status"]["enrolled_via_dep"]
                seen = datetime.strptime(
                    obj["general"]["last_contact_time"], "%Y-%m-%d %H:%M:%S"
                )
                now = datetime.utcnow()

            except IndexError:
                pass

            difference = (now - seen).days

        try:
            if (now - seen).days < 10 and not args.os:  # if recent
                recent_computers.append(
                    f"{x} {macos}\t"
                    + f"name : {name}\n"
                    + f"\t\tDEP  : {dep}\n"
                    + f"\t\tseen : {difference} days ago"
                )

            if (now - seen).days < 10 and args.os and (macos >= args.os):
                compliant.append(
                    f"{x} {macos}\t"
                    + f"name : {name}\n"
                    + f"\t\tDEP  : {dep}\n"
                    + f"\t\tseen : {difference} days ago"
                )
            elif (now - seen).days < 10 and args.os and (macos < args.os):
                warning.append(
                    f"{x} {macos}\t"
                    + f"name : {name}\n"
                    + f"\t\tDEP  : {dep}\n"
                    + f"\t\tseen : {difference} days ago"
                )

            if (now - seen).days > 10:
                old_computers.append(
                    f"{x} {macos}\t"
                    + f"name : {name}\n"
                    + f"\t\tDEP  : {dep}\n"
                    + f"\t\tseen : {difference} days ago"
                )

        except IndexError:
            print("checkin calc. error")

            # recent_computers.remove(f"{macos} {name} dep:{dep} seen:{calc}")

    # query is done
    print(Bcolors.OKCYAN + "Loading complete...\n\nSummary:" + Bcolors.ENDC)

    if args.os:
        # summarise os
        if compliant:
            print(f"{len(compliant)} compliant and recent:")
            for x in compliant:
                print(Bcolors.OKGREEN + x + Bcolors.ENDC)
        if warning:
            print(f"{len(warning)} non-compliant:")
            for x in warning:
                print(Bcolors.WARNING + x + Bcolors.ENDC)
        if old_computers:
            print(f"{len(old_computers)} stale - OS version not considered:")
            for x in old_computers:
                print(Bcolors.FAIL + x + Bcolors.ENDC)
    else:
        # regular summary
        print(f"{len(recent_computers)} last check-in within the past 10 days")
        for x in recent_computers:
            print(Bcolors.OKGREEN + x + Bcolors.ENDC)
        print(f"{len(old_computers)} stale - last check-in more than 10 days")
        for x in old_computers:
            print(Bcolors.FAIL + x + Bcolors.ENDC)

    if args.slack:
        # send a slack api webhook with this number
        score = len(recent_computers) / (len(old_computers) + len(recent_computers))
        score = f"{score:.2%}"
        slack_payload = str(
            f":hospital: update health: {score} - {len(old_computers)} "
            f"need to be fixed on {jamf_url}\n"
        )
        print(slack_payload)

        data = {"text": slack_payload}
        for x in old_computers:
            print(Bcolors.WARNING + x + Bcolors.ENDC)
            slack_payload += str(f"{x}\n")

        data = {"text": slack_payload}
        url = slack_webhook
        request_type = "POST"
        curl.request(request_type, url, token, verbosity, data)


def handle_policies(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling policies"""
    # declare the csv data for export
    csv_fields = [
        "policy_id",
        "policy_name",
        "policy_enabled",
        "policy_category",
        "pkg",
        "scope",
        "exclusions",
    ]
    csv_data = []
    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)
    if args.all or args.disabled or args.unused:
        if args.disabled:
            csv_write = os.path.join(csv_path, "Policies", "Disabled", csv_file)
        if args.unused:
            csv_write = os.path.join(csv_path, "Policies", "Unused", csv_file)
        else:
            csv_write = os.path.join(csv_path, "Policies", "All", csv_file)

        categories = api_get.get_uapi_obj_list(jamf_url, "category", token, verbosity)
        if verbosity > 1:
            print(f"Categories: {categories}")
        if categories:
            disabled_policies = {}
            unused_policies = {}
            for category in categories:
                # loop all the categories
                print(
                    Bcolors.OKCYAN
                    + f"category {category['id']}\t{category['name']}"
                    + Bcolors.ENDC
                )
                policies = api_get.get_policies_in_category(
                    jamf_url, category["id"], token, verbosity
                )
                if policies:
                    # loop through all the policies
                    for policy in policies:
                        groups = []
                        exclusion_groups = []
                        generic_info = api_get.get_api_obj_value_from_id(
                            jamf_url, "policy", policy["id"], "", token, verbosity
                        )
                        # get scope
                        if generic_info["scope"]["all_computers"]:
                            groups.append("All Computers")
                            unused = "false"
                        else:
                            g_count = len(generic_info["scope"]["computer_groups"])
                            for g in range(g_count):
                                groups.append(
                                    generic_info["scope"]["computer_groups"][g][
                                        "name"
                                    ]  # noqa: E501
                                )
                            if len(groups) < 1:
                                unused = "true"
                            else:
                                unused = "false"
                        eg_count = len(
                            generic_info["scope"]["exclusions"][
                                "computer_groups"
                            ]  # noqa: E501
                        )
                        for eg in range(eg_count):
                            exclusion_groups.append(
                                generic_info["scope"]["exclusions"]["computer_groups"][
                                    eg
                                ][
                                    "name"
                                ]  # noqa: E501
                            )
                        # get enabled status
                        if generic_info["general"]["enabled"] is True:
                            enabled = "true"
                        else:
                            enabled = "false"
                        # get packages
                        try:
                            pkg = generic_info["package_configuration"]["packages"][0][
                                "name"
                            ]  # noqa: E501
                        except IndexError:
                            pkg = "none"

                        # now show all the policies as each category loops
                        if enabled == "false":
                            disabled_policies[policy["id"]] = policy["name"]
                            if verbosity > 1:
                                print(
                                    f"Number of disabled policies: {len(disabled_policies)}"  # noqa: E501
                                )
                        if unused == "true":
                            unused_policies[policy["id"]] = policy["name"]
                            if verbosity > 1:
                                print(
                                    f"Number of unused policies: {len(unused_policies)}"  # noqa: E501
                                )
                        do_print = 1
                        if args.disabled and enabled == "true":
                            do_print = 0
                        if args.unused and unused == "false":
                            do_print = 0
                        if do_print == 1:
                            print(
                                Bcolors.WARNING
                                + f"  policy {policy['id']}"
                                + f"\tname       : {policy['name']}\n"
                                + Bcolors.ENDC
                                + f"\t\tenabled    : {enabled}\n"
                                + f"\t\tpkg        : {pkg}\n"
                                + f"\t\tscope      : {groups}\n"
                                + f"\t\texclusions : {exclusion_groups}"
                            )
                            csv_data.append(
                                {
                                    "policy_id": policy["id"],
                                    "policy_name": policy["name"],
                                    "policy_category": category["name"],
                                    "policy_enabled": enabled,
                                    "pkg": pkg,
                                    "scope": groups,
                                    "exclusions": exclusion_groups,
                                }
                            )

            pathlib.Path(os.path.dirname(csv_write)).mkdir(parents=True, exist_ok=True)
            api_connect.write_csv_file(csv_write, csv_fields, csv_data)
            print(
                "\n"
                + Bcolors.OKGREEN
                + f"CSV file written to {csv_write}"
                + Bcolors.ENDC
            )

            id_list = ""
            if (len(disabled_policies) > 0 or len(unused_policies) > 0) and args.delete:
                if args.disabled:
                    policy_type = "disabled"
                    policies_to_act_on = disabled_policies
                else:
                    policy_type = "unused"
                    policies_to_act_on = unused_policies
                if actions.confirm(
                    prompt=(
                        f"\nDelete all {policy_type} policies?"
                        "\n(press n to go on to confirm individually)?"
                    ),
                    default=False,
                ):
                    delete_all = True
                else:
                    delete_all = False

                # Enter the IDs of the policies you want to delete
                if not delete_all:
                    for policy_id, policy_name in policies_to_act_on.items():
                        print(policy_id, ":", policy_name)

                    chosen_ids = input(
                        "Enter the IDs of the policies you want to delete, "
                        "or leave blank to go through all: "
                    )
                    id_list = chosen_ids.split()

                # prompt to delete each policy in turn
                for policy_id, policy_name in policies_to_act_on.items():
                    if delete_all or (
                        (policy_id in id_list or not id_list)
                        and actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete [{policy_id}] {policy_name}?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        )
                    ):
                        print(f"Deleting {policy_name}...")
                        status_code = api_delete.delete_api_object(
                            jamf_url, "policy", policy_id, token, verbosity
                        )
                        if args.slack:
                            send_slack_notification(
                                jamf_url,
                                args.user,
                                slack_webhook,
                                "policy",
                                policy_name,
                                "delete",
                                status_code,
                            )

        else:
            print("something went wrong: no categories found.")

        print(
            "\n"
            + Bcolors.OKGREEN
            + f"All policies listed above... program complete for {jamf_url}"
            + Bcolors.ENDC
        )

    elif args.search:
        query = args.search
        csv_write = os.path.join(csv_path, "Policies", "Search", csv_file)

        policies = api_get.get_api_obj_list(jamf_url, "policy", token, verbosity)

        if policies:
            # targets is the new list
            targets = []
            print(
                f"Searching {len(policies)} policy/ies on {jamf_url}:\n"
                "To delete policies, obtain a matching query, "
                "then run with the "
                "--delete argument"
            )

            for x in query:
                for policy in policies:
                    # do the actual search
                    if x in policy["name"]:
                        targets.append(policy.copy())

            if len(targets) > 0:
                print("Policies found:")
                for target in targets:
                    print(
                        Bcolors.WARNING
                        + f"- policy {target['id']}"
                        + f"\tname  : {target['name']}"
                        + Bcolors.ENDC
                    )
                    csv_data.append(
                        {
                            "policy_id": target["id"],
                            "policy_name": target["name"],
                        }
                    )
                    pathlib.Path(os.path.dirname(csv_write)).mkdir(
                        parents=True, exist_ok=True
                    )
                    api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                    print(
                        "\n"
                        + Bcolors.OKGREEN
                        + f"CSV file written to {csv_write}"
                        + Bcolors.ENDC
                    )
                    if args.delete:
                        api_delete.delete_api_object(
                            jamf_url, "policy", target["id"], token, verbosity
                        )
                print(f"{len(targets)} total matches")
            else:
                for partial in query:
                    print(f"No match found: {partial}")

    else:
        sys.exit("ERROR: with --policies you must supply --search or --all.")


def handle_policies_from_csv_data(jamf_url, token, args, slack_webhook, verbosity):
    """Function for deleting policies based on IDs in a CSV"""
    # import csv
    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)
    csv_read = os.path.join(csv_path, "Policies", "To Delete", csv_file)

    print("\nPolicies:\n")

    # generate a list of IDs from the csv
    # with open(csv_read, "r", encoding="utf-8") as csvdata:
    # creating a csv dict reader object
    reader = csv.DictReader(
        open(csv_read, "r", encoding="utf-8"),
        delimiter=";",
    )
    for row in reader:
        policy_id = row["policy_id"]
        policy_name = row["policy_name"]
        print(f"[{policy_id}] - {policy_name}")

    # confirm All or per item
    if args.delete:
        if actions.confirm(
            prompt=(
                "\nDelete all listed policies?"
                "\n(press n to go on to confirm individually)?"
            ),
            default=False,
        ):
            delete_all = True
        else:
            delete_all = False

        id_list = ""
        # Enter the IDs of the policies you want to delete
        if not delete_all:
            chosen_ids = input(
                "Enter the IDs of the policies you want to delete or leave blank to go through all: "
            )
            id_list = chosen_ids.split()

        # prompt to delete each policy in turn
        reader = csv.DictReader(
            open(csv_read, "r", encoding="utf-8"),
            delimiter=";",
        )
        for row in reader:
            policy_id = row["policy_id"]
            policy_name = row["policy_name"]
            if delete_all or (
                (policy_id in id_list or not id_list)
                and actions.confirm(
                    prompt=(
                        Bcolors.OKBLUE
                        + f"Delete [{policy_id}] - {policy_name}?"
                        + Bcolors.ENDC
                    ),
                    default=False,
                )
            ):
                print(f"Deleting {policy_name}...")
                status_code = api_delete.delete_api_object(
                    jamf_url, "policy", policy_id, token, verbosity
                )

                if args.slack:
                    send_slack_notification(
                        jamf_url,
                        args.user,
                        slack_webhook,
                        "policy",
                        policy_name,
                        "delete",
                        status_code,
                    )


def handle_policies_in_category(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling policies in a specific category"""
    csv_fields = [
        "policy_id",
        "policy_name",
        "category",
    ]
    csv_data = []

    categories = args.category
    # print(f"categories to check are:\n{categories}\nTotal: {len(categories)}")
    # now process the list of categories
    for category in categories:
        # create more specific output filename
        csv_path = os.path.dirname(args.csv)
        csv_file = os.path.basename(args.csv)
        csv_write = os.path.join(csv_path, "Policies", "Categories", category, csv_file)

        category = category.replace(" ", "%20")
        # return all policies found in each category
        print(f"\nChecking '{category}' on {jamf_url}")
        obj = api_get.get_policies_in_category(jamf_url, category, token, verbosity)
        if obj:
            if not args.delete:
                print(
                    f"Category '{category}' exists with {len(obj)} policies: "
                    "To delete them run this command again "
                    "with the --delete flag."
                )

            policies_in_category = {}

            for obj_item in obj:
                policies_in_category[obj_item["id"]] = obj_item["name"]

            for policy_id, policy_name in policies_in_category.items():
                print(Bcolors.FAIL + f"[{policy_id}] " + policy_name + Bcolors.ENDC)
                csv_data.append(
                    {
                        "policy_id": policy_id,
                        "policy_name": policy_name,
                        "category": category,
                    }
                )

            if args.delete:
                if actions.confirm(
                    prompt=(
                        f"\nDelete all policies in category '{category}'?"
                        "\n(press n to go on to confirm individually)?"
                    ),
                    default=False,
                ):
                    delete_all = True
                else:
                    delete_all = False

                # Enter the IDs of the policies you want to delete
                if not delete_all:
                    chosen_ids = input(
                        "Enter the IDs of the policies you want to delete or leave blank to go through all: "
                    )
                    id_list = chosen_ids.split()

                id_list = ""
                # prompt to delete each package in turn
                for policy_id, policy_name in policies_in_category.items():
                    if delete_all or (
                        (policy_id in id_list or not id_list)
                        and actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete [{policy_id}] {policy_name}?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        )
                    ):
                        print(f"Deleting {policy_name}...")
                        status_code = api_delete.delete_api_object(
                            jamf_url, "policy", policy_id, token, verbosity
                        )
                        if args.slack:
                            send_slack_notification(
                                jamf_url,
                                args.user,
                                slack_webhook,
                                "policy",
                                policy_name,
                                "delete",
                                status_code,
                            )

            pathlib.Path(os.path.dirname(csv_write)).mkdir(parents=True, exist_ok=True)
            api_connect.write_csv_file(csv_write, csv_fields, csv_data)
            print(
                "\n"
                + Bcolors.OKGREEN
                + f"CSV file written to {csv_write}"
                + Bcolors.ENDC
            )
        else:
            print(f"Category '{category}' not found")


def handle_policy_list(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling a search list of policies"""
    policy_names = args.names
    print(
        "policy names to check are:\n" f"{policy_names}\n" f"Total: {len(policy_names)}"
    )

    for policy_name in policy_names:
        print(f"\nChecking '{policy_name}' on {jamf_url}")

        obj_id = api_get.get_api_obj_id_from_name(
            jamf_url, "policy", policy_name, token, verbosity
        )

        if obj_id:
            # gather info from interesting parts of the policy API
            # use a single call
            # general/name
            # scope/computer_gropus  [0]['name']
            generic_info = api_get.get_api_obj_value_from_id(
                jamf_url, "policy", obj_id, "", token, verbosity
            )
            name = generic_info["general"]["name"]
            try:
                groups = generic_info["scope"]["computer_groups"][0]["name"]
            except IndexError:
                groups = ""

            print(f"Match found: '{name}' ID: {obj_id} Group: {groups}")
            if args.delete:
                status_code = api_delete.delete_api_object(
                    jamf_url, "policy", obj_id, token, verbosity
                )
                if args.slack:
                    send_slack_notification(
                        jamf_url,
                        args.user,
                        slack_webhook,
                        "policy",
                        policy_name,
                        "delete",
                        status_code,
                    )
        else:
            print(f"Policy '{policy_name}' not found")


def handle_profiles(jamf_url, api_endpoint, token, args, slack_webhook, verbosity):
    """Function for handling profiles"""
    # declare the csv data for export
    csv_fields = [
        "profile_id",
        "profile_name",
        "category",
        "distribution_method",
        "scope",
        "exclusions",
    ]
    csv_data = []
    if args.all or args.unused:
        profiles = api_get.get_api_obj_list(jamf_url, api_endpoint, token, verbosity)

        # create more specific output filename
        csv_path = os.path.dirname(args.csv)
        csv_file = os.path.basename(args.csv)

        if "os_x" in api_endpoint:
            profile_type = "Computer Profiles"
        else:
            profile_type = "Mobile Device Profiles"
        if args.unused:
            csv_write = os.path.join(csv_path, profile_type, "Unused", csv_file)
        else:
            csv_write = os.path.join(csv_path, profile_type, "All", csv_file)
        if profiles:
            unused_profiles = {}
            for profile in profiles:
                # loop all the profiles
                groups = []
                exclusion_groups = []
                unused = "false"
                if args.details or args.unused:
                    # gather interesting info for each profile via API
                    results = api_get.get_api_obj_from_id(
                        jamf_url, api_endpoint, profile["id"], token, verbosity
                    )
                    generic_info = results[api_endpoint]

                    category = generic_info["general"]["category"]["name"]
                    try:  # macOS
                        distribution_method = generic_info["general"][
                            "distribution_method"
                        ]
                    except KeyError:  # iOS
                        distribution_method = generic_info["general"][
                            "deployment_method"
                        ]
                    # get scope
                    if args.macosprofiles:
                        if generic_info["scope"]["all_computers"]:
                            groups = ["All Computers"]
                            unused = "false"
                        else:
                            g_count = len(generic_info["scope"]["computer_groups"])
                            for g in range(g_count):
                                groups.append(
                                    generic_info["scope"]["computer_groups"][g][
                                        "name"
                                    ]  # noqa: E501
                                )
                            if len(groups) < 1:
                                unused = "true"
                        eg_count = len(
                            generic_info["scope"]["exclusions"][
                                "computer_groups"
                            ]  # noqa: E501
                        )
                        for eg in range(eg_count):
                            exclusion_groups.append(
                                generic_info["scope"]["exclusions"]["computer_groups"][
                                    eg
                                ][
                                    "name"
                                ]  # noqa: E501
                            )
                    else:
                        if generic_info["scope"]["all_mobile_devices"]:
                            groups = ["All Mobile Devices"]
                        else:
                            g_count = len(generic_info["scope"]["mobile_device_groups"])
                            for g in range(g_count):
                                groups.append(
                                    generic_info["scope"]["mobile_device_groups"][g][
                                        "name"
                                    ]  # noqa: E501
                                )
                            if len(groups) < 1:
                                unused = "true"
                        eg_count = len(
                            generic_info["scope"]["exclusions"][
                                "mobile_device_groups"
                            ]  # noqa: E501
                        )
                        for eg in range(eg_count):
                            exclusion_groups.append(
                                generic_info["scope"]["exclusions"][
                                    "mobile_device_groups"
                                ][eg][
                                    "name"
                                ]  # noqa: E501
                            )
                    if unused == "true":
                        unused_profiles[profile["id"]] = profile["name"]
                        if verbosity:
                            print(
                                f"Number of unused profiles: {len(unused_profiles)}"  # noqa: E501
                            )
                    do_print = 1
                    if args.unused and unused == "false":
                        do_print = 0
                    if do_print == 1:
                        print(
                            Bcolors.WARNING
                            + f"  profile {profile['id']}"
                            + f"\tname                : {profile['name']}\n"
                            + Bcolors.ENDC
                            + f"\t\tcategory            : {category}\n"
                            + f"\t\tdistribution_method : {distribution_method}\n"
                            + f"\t\tscope               : {groups}\n"
                            + f"\t\texclusions          : {exclusion_groups}"
                        )

                        csv_data.append(
                            {
                                "profile_id": profile["id"],
                                "profile_name": profile["name"],
                                "category": category,
                                "distribution_method": distribution_method,
                                "scope": groups,
                                "exclusions": exclusion_groups,
                            }
                        )
                else:
                    print(
                        Bcolors.WARNING
                        + f"  profile {profile['id']}\n"
                        + f"      name     : {profile['name']}"
                        + Bcolors.ENDC
                    )
            if args.details or args.unused:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )

            id_list = ""
            if len(unused_profiles) > 0 and args.delete:
                if actions.confirm(
                    prompt=(
                        f"\nDelete all unused {api_endpoint}?"
                        "\n(press n to go on to confirm individually)?"
                    ),
                    default=False,
                ):
                    delete_all = True
                else:
                    delete_all = False

                # Enter the IDs of the policies you want to delete
                if not delete_all:
                    for profile_id, profile_name in unused_profiles.items():
                        print(profile_id, ":", profile_name)

                    chosen_ids = input(
                        f"Enter the IDs of the {api_endpoint} you want to delete, "
                        "or leave blank to go through all: "
                    )
                    id_list = chosen_ids.split()

                # prompt to delete each policy in turn
                for profile_id, profile_name in unused_profiles.items():
                    if delete_all or (
                        (profile_id in id_list or not id_list)
                        and actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete [{profile_id}] {profile_name}?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        )
                    ):
                        print(f"Deleting {profile_name}...")
                        status_code = api_delete.delete_api_object(
                            jamf_url, api_endpoint, profile_id, token, verbosity
                        )
                        if args.slack:
                            send_slack_notification(
                                jamf_url,
                                args.user,
                                slack_webhook,
                                api_endpoint,
                                profile_name,
                                "delete",
                                status_code,
                            )

        else:
            print("\nNo profiles found")
    else:
        exit("ERROR: with --computerprofiles you must supply --all or --unused.")


def handle_advancedsearches(jamf_url, api_endpoint, token, args, verbosity):
    """Function for handling advanced searches"""
    # declare the csv data for export
    csv_fields = ["search_id", "search_name"]
    csv_data = []
    if args.all:
        # create more specific output filename
        csv_path = os.path.dirname(args.csv)
        csv_file = os.path.basename(args.csv)
        csv_write = os.path.join(csv_path, "Advanced Searches", csv_file)

        advancedsearches = api_get.get_api_obj_list(
            jamf_url, api_endpoint, token, verbosity
        )
        if advancedsearches:
            for search in advancedsearches:
                # loop all the advancedsearches
                print(
                    Bcolors.WARNING
                    + f"  advancedsearch {search['id']}\n"
                    + f"      name     : {search['name']}"
                    + Bcolors.ENDC
                )
                csv_data.append(
                    {
                        "search_id": search["id"],
                        "search_name": search["name"],
                    }
                )

            if args.details:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(args.csv, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )

        else:
            print("\nNo profiles found")
    else:
        exit("ERROR: with --computerprofiles you must supply --all.")


def handle_packages(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling packages"""
    unused_packages = {}
    used_packages = {}
    if args.unused:
        # get a list of packages in prestage enrollments
        packages_in_prestages = api_get.get_packages_in_prestages(
            jamf_url, token, verbosity
        )
        # get a list of packages in patch software titles
        packages_in_titles = api_get.get_packages_in_patch_titles(
            jamf_url, token, verbosity
        )
        # get a list of packages in policies
        packages_in_policies = api_get.get_packages_in_policies(
            jamf_url, token, verbosity
        )
    else:
        packages_in_policies = []
        packages_in_titles = []
        packages_in_prestages = []

    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)
    csv_fields = ""
    csv_write = ""
    if args.all or args.unused:
        packages = api_get.get_api_obj_list(jamf_url, "package", token, verbosity)
        if packages:
            # declare the csv data for export
            if args.unused:
                csv_fields = ["pkg_id", "pkg_name", "used"]
                csv_data = []
                csv_write = os.path.join(csv_path, "Packages", "Unused", csv_file)
            elif args.details:
                csv_fields = [
                    "pkg_id",
                    "pkg_name",
                    "filename",
                    "category",
                    "info",
                    "notes",
                ]
                csv_data = []
                csv_write = os.path.join(csv_path, "Packages", "All", csv_file)
            for package in packages:
                # loop all the packages
                if args.unused:
                    # see if the package is in any policies
                    unused_in_policies = 0
                    unused_in_titles = 0
                    unused_in_prestages = 0
                    if packages_in_policies:
                        if package["name"] not in packages_in_policies:
                            unused_in_policies = 1
                    else:
                        unused_in_policies = 1
                    if packages_in_titles:
                        if package["name"] not in packages_in_titles:
                            unused_in_titles = 1
                    else:
                        unused_in_titles = 1
                    if packages_in_prestages:
                        if package["name"] not in packages_in_prestages:
                            unused_in_prestages = 1
                    else:
                        unused_in_prestages = 1
                    if (
                        unused_in_policies == 1
                        and unused_in_titles == 1
                        and unused_in_prestages == 1
                    ):
                        unused_packages[package["id"]] = package["name"]
                        csv_data.append(
                            {
                                "pkg_id": package["id"],
                                "pkg_name": package["name"],
                                "used": "false",
                            }
                        )
                    elif package["name"] not in used_packages:
                        used_packages[package["id"]] = package["name"]
                        csv_data.append(
                            {
                                "pkg_id": package["id"],
                                "pkg_name": package["name"],
                                "used": "true",
                            }
                        )
                else:
                    print(
                        Bcolors.WARNING
                        + f"  package {package['id']}\n"
                        + f"      name     : {package['name']}"
                        + Bcolors.ENDC
                    )
                    if args.details:
                        # gather interesting info for each package via API
                        generic_info = api_get.get_api_obj_value_from_id(
                            jamf_url, "package", package["id"], "", token, verbosity
                        )

                        filename = generic_info["filename"]
                        print(f"      filename : {filename}")
                        category = generic_info["category"]
                        if category and "No category assigned" not in category:
                            print(f"      category : {category}")
                        info = generic_info["info"]
                        if info:
                            print(f"      info     : {info}")
                        notes = generic_info["notes"]
                        if notes:
                            print(f"      notes    : {notes}")
                        csv_data.append(
                            {
                                "pkg_id": package["id"],
                                "pkg_name": package["name"],
                                "filename": filename,
                                "category": category,
                                "info": info,
                                "notes": notes,
                            }
                        )
            if args.details or args.unused:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )
            if args.unused:
                print(
                    "\nThe following packages are found in at least one "
                    "policy, PreStage Enrollment, and/or patch title:\n"
                )
                for pkg_name in used_packages.values():
                    print(Bcolors.OKGREEN + pkg_name + Bcolors.ENDC)

                print(
                    "\nThe following packages are not used in any policies, "
                    "PreStage Enrollments, or patch titles:\n"
                )
                for pkg_id, pkg_name in unused_packages.items():
                    print(Bcolors.FAIL + f"[{pkg_id}] " + pkg_name + Bcolors.ENDC)

                if args.delete:
                    if actions.confirm(
                        prompt=(
                            "\nDelete all unused packages?"
                            "\n(press n to go on to confirm individually)?"
                        ),
                        default=False,
                    ):
                        delete_all = True
                    else:
                        delete_all = False

                    # Enter the IDs of the policies you want to delete
                    id_list = ()
                    if not delete_all:
                        chosen_ids = input(
                            "Enter the IDs of the packages you want to delete, "
                            "or leave blank to go through all: "
                        )
                        id_list = chosen_ids.split()

                    for pkg_id, pkg_name in unused_packages.items():
                        # prompt to delete each package in turn
                        if delete_all or (
                            (pkg_id in id_list or not id_list)
                            and actions.confirm(
                                prompt=(
                                    Bcolors.OKBLUE
                                    + f"Delete [{pkg_id}] {pkg_name}?"
                                    + Bcolors.ENDC
                                ),
                                default=False,
                            )
                        ):
                            print(f"Deleting {pkg_name}...")
                            status_code = api_delete.delete_api_object(
                                jamf_url, "package", pkg_id, token, verbosity
                            )
                            # process for SMB shares if defined
                            if args.smb_url:
                                # mount the share
                                smb_actions.mount_smb(
                                    args.smb_url,
                                    args.smb_user,
                                    args.smb_pass,
                                    verbosity,
                                )
                                # delete the file from the share
                                smb_actions.delete_pkg(args.smb_url, pkg_name)
                                # unmount the share
                                smb_actions.umount_smb(args.smb_url)

                            if args.slack:
                                send_slack_notification(
                                    jamf_url,
                                    args.user,
                                    slack_webhook,
                                    "package",
                                    pkg_name,
                                    "delete",
                                    status_code,
                                )
    elif args.search:
        query = args.search
        csv_fields = ["pkg_id", "pkg_name"]
        csv_data = []
        csv_write = os.path.join(csv_path, "Packages", "Search", csv_file)

        packages = api_get.get_api_obj_list(jamf_url, "package", token, verbosity)

        if packages:
            # targets is the new list
            targets = []
            print(
                f"Searching {len(packages)} packages on {jamf_url}:\n"
                "To delete packages, obtain a matching query, "
                "then run with the "
                "--delete argument"
            )

            for x in query:
                for pkg in packages:
                    # do the actual search
                    if x in pkg["name"]:
                        targets.append(pkg.copy())

            if len(targets) > 0:
                print("Packages found:")
                for target in targets:
                    print(
                        Bcolors.WARNING
                        + f"- package {target['id']}"
                        + f"\tname  : {target['name']}"
                        + Bcolors.ENDC
                    )
                    csv_data.append(
                        {
                            "pkg_id": target["id"],
                            "pkg_name": target["name"],
                        }
                    )
                    if args.delete:
                        status_code = api_delete.delete_api_object(
                            jamf_url, "package", target["id"], token, verbosity
                        )
                        if args.smb_url:
                            # mount the share
                            smb_actions.mount_smb(
                                args.smb_url,
                                args.smb_user,
                                args.smb_pass,
                                verbosity,
                            )
                            # delete the file from the share
                            smb_actions.delete_pkg(args.smb_url, target["name"])
                            # unmount the share
                            smb_actions.umount_smb(args.smb_url)

                        if args.slack:
                            send_slack_notification(
                                jamf_url,
                                args.user,
                                slack_webhook,
                                "package",
                                pkg_name,
                                "delete",
                                status_code,
                            )
                print(f"{len(targets)} total matches")
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )
            else:
                for partial in query:
                    print(f"No match found: {partial}")

    else:
        exit("ERROR: with --packages you must supply --unused, --search or --all.")


def handle_scripts(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling scripts"""
    unused_scripts = {}
    used_scripts = {}
    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)

    if args.unused:
        # get a list of scripts in policies
        scripts_in_policies = api_get.get_scripts_in_policies(
            jamf_url, token, verbosity
        )
        csv_write = os.path.join(csv_path, "Scripts", "Unused", csv_file)

    else:
        scripts_in_policies = []
        csv_write = os.path.join(csv_path, "Scripts", "All", csv_file)

    if args.all or args.unused:
        csv_fields = [
            "script_id",
            "script_name",
            "category",
            "info",
            "notes",
            "priority",
        ]
        csv_data = []
        scripts = api_get.get_uapi_obj_list(jamf_url, "script", token, verbosity)
        if scripts:
            for script in scripts:
                # loop all the scripts
                if args.unused:
                    # see if the script is in any smart groups
                    unused_in_policies = 0
                    if scripts_in_policies:
                        if script["name"] not in scripts_in_policies:
                            unused_in_policies = 1
                    else:
                        unused_in_policies = 1
                    if unused_in_policies == 1:
                        unused_scripts[script["id"]] = script["name"]
                    elif script["name"] not in used_scripts:
                        used_scripts[script["id"]] = script["name"]
                else:
                    print(
                        Bcolors.WARNING
                        + f"  script {script['id']}\n"
                        + f"      name     : {script['name']}"
                        + Bcolors.ENDC
                    )
                    if args.details:
                        # gather interesting info for each script via API
                        generic_info = api_get.get_uapi_obj_from_id(
                            jamf_url, "script", script["id"], token, verbosity
                        )

                        category = generic_info["categoryName"]
                        if category and "No category assigned" not in category:
                            print(f"      category : {category}")
                        info = generic_info["info"]
                        if info:
                            print(f"      info     : {info}")
                        notes = generic_info["notes"]
                        if notes:
                            print(f"      notes    : {notes}")
                        priority = generic_info["priority"]
                        print(f"      priority  : {priority}")
                        csv_data.append(
                            {
                                "script_id": script["id"],
                                "script_name": script["name"],
                                "category": category,
                                "info": info,
                                "notes": notes,
                                "priority": priority,
                            }
                        )

            if args.details:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )

            if args.unused:
                print("\nThe following scripts are found in " "at least one policy:\n")
                for script_name in used_scripts.values():
                    print(Bcolors.OKGREEN + script_name + Bcolors.ENDC)

                print("\nThe following scripts are not used in any policies:\n")
                for script_id, script_name in unused_scripts.items():
                    print(Bcolors.FAIL + f"[{script_id}] " + script_name + Bcolors.ENDC)

                if args.delete:
                    if actions.confirm(
                        prompt=(
                            "\nDelete all unused scripts?"
                            "\n(press n to go on to confirm individually)?"
                        ),
                        default=False,
                    ):
                        delete_all = True
                    else:
                        delete_all = False
                    for script_id, script_name in unused_scripts.items():
                        # prompt to delete each script in turn
                        if delete_all or actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete {script_name} (id={script_id})?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        ):
                            print(f"Deleting {script_name}...")
                            status_code = api_delete.delete_uapi_object(
                                jamf_url, "script", script_id, token, verbosity
                            )
                            if args.slack:
                                send_slack_notification(
                                    jamf_url,
                                    args.user,
                                    slack_webhook,
                                    "script",
                                    script_name,
                                    "delete",
                                    status_code,
                                )
        else:
            print("\nNo scripts found")
    else:
        exit("ERROR: with --scripts you must supply --unused or --all.")


def handle_eas(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling extension attributes"""
    csv_fields = [
        "ea_id",
        "ea_name",
        "enabled",
        "data_type",
        "input_type",
        "inventory_display",
    ]
    csv_data = []
    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)

    unused_eas = {}
    used_eas = {}
    if args.unused:
        criteria_in_computer_groups = api_get.get_criteria_in_computer_groups(
            jamf_url, token, verbosity
        )
        names_in_advanced_searches = api_get.get_names_in_advanced_searches(
            jamf_url, token, verbosity
        )
        csv_write = os.path.join(csv_path, "Extension Attributes", "Unused", csv_file)
        # TODO EAs in Patch policies?

    else:
        criteria_in_computer_groups = []
        names_in_advanced_searches = []
        csv_write = os.path.join(csv_path, "Extension Attributes", "All", csv_file)

    if args.all or args.unused:
        eas = api_get.get_api_obj_list(
            jamf_url, "extension_attribute", token, verbosity
        )
        if eas:
            for ea in eas:
                # loop all the eas
                if args.unused:
                    # see if the eas is in any policies
                    unused_in_computer_groups = 0
                    unused_in_advanced_searches = 0
                    if criteria_in_computer_groups:
                        if ea["name"] not in criteria_in_computer_groups:
                            unused_in_computer_groups = 1
                    else:
                        unused_in_computer_groups = 1
                    if names_in_advanced_searches:
                        if ea["name"] not in names_in_advanced_searches:
                            unused_in_advanced_searches = 1
                    else:
                        unused_in_advanced_searches = 1
                    if (
                        unused_in_computer_groups == 1
                        and unused_in_advanced_searches == 1
                    ):
                        unused_eas[ea["id"]] = ea["name"]
                    elif ea["name"] not in used_eas:
                        used_eas[ea["id"]] = ea["name"]
                else:
                    print(
                        Bcolors.WARNING
                        + f"  EA {ea['id']}\n"
                        + f"      name     : {ea['name']}"
                        + Bcolors.ENDC
                    )
                    if args.details:
                        # gather interesting info for each EA via API
                        result = api_get.get_api_obj_from_id(
                            jamf_url,
                            "extension_attribute",
                            ea["id"],
                            token,
                            verbosity,
                        )
                        generic_info = result["computer_extension_attribute"]
                        enabled = generic_info["enabled"]
                        print(f"      enabled            : {enabled}")
                        data_type = generic_info["data_type"]
                        print(f"      data_type          : {data_type}")
                        input_type = generic_info["input_type"]["type"]
                        print(f"      input_type              : {input_type}")
                        inventory_display = generic_info["inventory_display"]
                        print(f"      inventory_display  : {inventory_display}")
                        csv_data.append(
                            {
                                "ea_id": ea["id"],
                                "ea_name": ea["name"],
                                "enabled": enabled,
                                "data_type": data_type,
                                "input_type": input_type,
                                "inventory_display": inventory_display,
                            }
                        )

            if args.details:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )

            if args.unused:
                print(
                    "\nThe following EAs are found in at least one "
                    "smart group or advanced search:\n"
                )
                for ea_name in used_eas.values():
                    print(Bcolors.OKGREEN + ea_name + Bcolors.ENDC)

                print(
                    "\nThe following EAs are not used in any smart groups "
                    "or advanced searches:\n"
                )
                for ea_id, ea_name in unused_eas.items():
                    print(Bcolors.FAIL + f"[{ea_id}] " + ea_name + Bcolors.ENDC)

                if args.delete:
                    if actions.confirm(
                        prompt=(
                            "\nDelete all unused EAs?"
                            "\n(press n to go on to confirm individually)?"
                        ),
                        default=False,
                    ):
                        delete_all = True
                    else:
                        delete_all = False
                    for ea_id, ea_name in unused_eas.items():
                        # prompt to delete each EA in turn
                        if delete_all or actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete {ea_name} (id={ea_id})?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        ):
                            print(f"Deleting {ea_name}...")
                            status_code = api_delete.delete_api_object(
                                jamf_url,
                                "extension_attribute",
                                ea_id,
                                token,
                                verbosity,
                            )
                            if args.slack:
                                send_slack_notification(
                                    jamf_url,
                                    args.user,
                                    slack_webhook,
                                    "extension_attribute",
                                    ea_name,
                                    "delete",
                                    status_code,
                                )
        else:
            print("\nNo EAs found")
    else:
        exit("ERROR: with --ea you must supply --unused or --all.")


def handle_groups(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling computer groups"""
    csv_fields = ["group_id", "group_name", "is_smart"]
    csv_data = []
    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)

    unused_groups = {}
    used_groups = {}
    if args.unused:
        # look in computer groups for computer groups in the criteria
        criteria_in_computer_groups = api_get.get_criteria_in_computer_groups(
            jamf_url, token, verbosity
        )
        # look in advanced searches for computer groups in the criteria
        names_in_advanced_searches = api_get.get_names_in_advanced_searches(
            jamf_url, token, verbosity
        )
        # look in the scope of policies
        groups_in_policies = api_get.get_groups_in_api_objs(
            jamf_url, token, "policy", verbosity
        )
        # look in the scope of Mac App Store apps
        groups_in_mas_apps = api_get.get_groups_in_api_objs(
            jamf_url, token, "mac_application", verbosity
        )
        # look in the scope of configurator profiles
        groups_in_config_profiles = api_get.get_groups_in_api_objs(
            jamf_url, token, "os_x_configuration_profile", verbosity
        )
        # look in the scope of patch policies
        groups_in_patch_policies = api_get.get_groups_in_patch_policies(
            jamf_url, token, verbosity
        )
        # look in the scope of restricted software
        groups_in_restricted_software = api_get.get_groups_in_api_objs(
            jamf_url, token, "restricted_software", verbosity
        )
        csv_write = os.path.join(csv_path, "Computer Groups", "Unused", csv_file)

    else:
        criteria_in_computer_groups = []
        names_in_advanced_searches = []
        groups_in_policies = []
        groups_in_mas_apps = []
        groups_in_config_profiles = []
        groups_in_patch_policies = []
        groups_in_restricted_software = []
        csv_write = os.path.join(csv_path, "Computer Groups", "All", csv_file)

    if args.all or args.unused:
        groups = api_get.get_api_obj_list(jamf_url, "computer_group", token, verbosity)
        if groups:
            for group in groups:
                # loop all the groups
                if args.unused:
                    # see if the groups is in any smart groups
                    unused_in_computer_groups = 0
                    unused_in_advanced_searches = 0
                    unused_in_policies = 0
                    unused_in_mas_apps = 0
                    unused_in_config_profiles = 0
                    unused_in_patch_policies = 0
                    unused_in_restricted_software = 0

                    if criteria_in_computer_groups:
                        if group["name"] not in criteria_in_computer_groups:
                            unused_in_computer_groups = 1
                    else:
                        unused_in_computer_groups = 1

                    if names_in_advanced_searches:
                        if group["name"] not in names_in_advanced_searches:
                            unused_in_advanced_searches = 1
                    else:
                        unused_in_advanced_searches = 1

                    if groups_in_policies:
                        if group["name"] not in groups_in_policies:
                            unused_in_policies = 1
                    else:
                        unused_in_policies = 1

                    if groups_in_mas_apps:
                        if group["name"] not in groups_in_mas_apps:
                            unused_in_mas_apps = 1
                    else:
                        unused_in_mas_apps = 1

                    if groups_in_config_profiles:
                        if group["name"] not in groups_in_config_profiles:
                            unused_in_config_profiles = 1
                    else:
                        unused_in_config_profiles = 1

                    if groups_in_patch_policies:
                        if group["name"] not in groups_in_patch_policies:
                            unused_in_patch_policies = 1
                    else:
                        unused_in_patch_policies = 1

                    if groups_in_restricted_software:
                        if group["name"] not in groups_in_restricted_software:
                            unused_in_restricted_software = 1
                    else:
                        unused_in_restricted_software = 1

                    if (
                        unused_in_computer_groups == 1
                        and unused_in_advanced_searches == 1
                        and unused_in_policies == 1
                        and unused_in_mas_apps == 1
                        and unused_in_config_profiles == 1
                        and unused_in_patch_policies == 1
                        and unused_in_restricted_software == 1
                    ):
                        unused_groups[group["id"]] = group["name"]
                    elif group["name"] not in used_groups:
                        used_groups[group["id"]] = group["name"]
                else:
                    print(
                        Bcolors.WARNING
                        + f"  group {group['id']}\n"
                        + f"      name     : {group['name']}"
                        + Bcolors.ENDC
                    )
                    if args.details:
                        # gather interesting info for each group via API
                        generic_info = api_get.get_api_obj_from_id(
                            jamf_url,
                            "computer_group",
                            group["id"],
                            token,
                            verbosity,
                        )

                        is_smart = generic_info["computer_group"]["is_smart"]
                        print(f"      is smart            : {is_smart}")
                        csv_data.append(
                            {
                                "group_id": group["id"],
                                "group_name": group["name"],
                                "is_smart": is_smart,
                            }
                        )

            if args.details:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )

            if args.unused:
                print(
                    "\nThe following groups are criteria in at least one "
                    "smart group or advanced search,\n"
                    "and/or are scoped or excluded in at least one "
                    "policy, patch policy, Mac App Store app,\n"
                    "configuration profile or restricted software:\n"
                )
                for group_name in used_groups.values():
                    print(Bcolors.OKGREEN + group_name + Bcolors.ENDC)

                print(
                    "\nThe following groups are not found in any "
                    "smart groups, "
                    "advanced searches\n"
                    "policies, patch policies, Mac App Store apps, "
                    "configuration profiles or restricted software:\n"
                )
                for group_id, group_name in unused_groups.items():
                    print(Bcolors.FAIL + f"[{group_id}] " + group_name + Bcolors.ENDC)

                if args.delete:
                    if actions.confirm(
                        prompt=(
                            "\nDelete all unused groups?"
                            "\n(press n to go on to confirm individually)?"
                        ),
                        default=False,
                    ):
                        delete_all = True
                    else:
                        delete_all = False
                    for group_id, group_name in unused_groups.items():
                        # prompt to delete each group in turn
                        if delete_all or actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete {group_name} (id={group_id})?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        ):
                            print(f"Deleting {group_name}...")
                            status_code = api_delete.delete_api_object(
                                jamf_url,
                                "computer_group",
                                group_id,
                                token,
                                verbosity,
                            )
                            if args.slack:
                                send_slack_notification(
                                    jamf_url,
                                    args.user,
                                    slack_webhook,
                                    "computer_group",
                                    group_name,
                                    "delete",
                                    status_code,
                                )
        else:
            print("\nNo groups found")
    else:
        exit("ERROR: with --groups you must supply --unused or --all.")


def handle_ios_groups(jamf_url, token, args, slack_webhook, verbosity):
    """Function for handling mobile device groups"""
    csv_fields = ["group_id", "group_name", "is_smart"]
    csv_data = []
    # create more specific output filename
    csv_path = os.path.dirname(args.csv)
    csv_file = os.path.basename(args.csv)

    unused_groups = {}
    used_groups = {}
    if args.unused:
        # look in computer groups for computer groups in the criteria
        criteria_in_ios_groups = api_get.get_criteria_in_ios_groups(
            jamf_url, token, verbosity
        )
        # look in advanced searches for computer groups in the criteria
        names_in_ios_advanced_searches = api_get.get_names_in_ios_advanced_searches(
            jamf_url, token, verbosity
        )
        # look in the scope of App Store apps
        groups_in_ios_apps = api_get.get_groups_in_ios_api_objs(
            jamf_url, token, "mobile_device_application", verbosity
        )
        # look in the scope of configuration profiles
        groups_in_ios_config_profiles = api_get.get_groups_in_ios_api_objs(
            jamf_url, token, "configuration_profile", verbosity
        )
        csv_write = os.path.join(csv_path, "Mobile Device Groups", "Unused", csv_file)

    else:
        criteria_in_ios_groups = []
        names_in_ios_advanced_searches = []
        groups_in_ios_apps = []
        groups_in_ios_config_profiles = []
        csv_write = os.path.join(csv_path, "Mobile Device Groups", "All", csv_file)

    if args.all or args.unused:
        groups = api_get.get_api_obj_list(
            jamf_url, "mobile_device_group", token, verbosity
        )
        if groups:
            for group in groups:
                # loop all the groups
                if args.unused:
                    # see if the groups is in any smart groups
                    unused_in_ios_groups = 0
                    unused_in_ios_advanced_searches = 0
                    unused_in_ios_apps = 0
                    unused_in_ios_config_profiles = 0

                    if criteria_in_ios_groups:
                        if group["name"] not in criteria_in_ios_groups:
                            unused_in_ios_groups = 1
                    else:
                        unused_in_ios_groups = 1

                    if names_in_ios_advanced_searches:
                        if group["name"] not in names_in_ios_advanced_searches:
                            unused_in_ios_advanced_searches = 1
                    else:
                        unused_in_ios_advanced_searches = 1

                    if groups_in_ios_apps:
                        if group["name"] not in groups_in_ios_apps:
                            unused_in_ios_apps = 1
                    else:
                        unused_in_ios_apps = 1

                    if groups_in_ios_config_profiles:
                        if group["name"] not in groups_in_ios_config_profiles:
                            unused_in_ios_config_profiles = 1
                    else:
                        unused_in_ios_config_profiles = 1

                    if (
                        unused_in_ios_groups == 1
                        and unused_in_ios_advanced_searches == 1
                        and unused_in_ios_apps == 1
                        and unused_in_ios_config_profiles == 1
                    ):
                        unused_groups[group["id"]] = group["name"]
                    elif group["name"] not in used_groups:
                        used_groups[group["id"]] = group["name"]
                else:
                    print(
                        Bcolors.WARNING
                        + f"  group {group['id']}\n"
                        + f"      name     : {group['name']}"
                        + Bcolors.ENDC
                    )
                    if args.details:
                        # gather interesting info for each group via API
                        generic_info = api_get.get_api_obj_from_id(
                            jamf_url,
                            "mobile_device_group",
                            group["id"],
                            token,
                            verbosity,
                        )

                        is_smart = generic_info["mobile_device_group"]["is_smart"]
                        print(f"      is smart            : {is_smart}")
                        csv_data.append(
                            {
                                "group_id": group["id"],
                                "group_name": group["name"],
                                "is_smart": is_smart,
                            }
                        )

            if args.details:
                pathlib.Path(os.path.dirname(csv_write)).mkdir(
                    parents=True, exist_ok=True
                )
                api_connect.write_csv_file(csv_write, csv_fields, csv_data)
                print(
                    "\n"
                    + Bcolors.OKGREEN
                    + f"CSV file written to {csv_write}"
                    + Bcolors.ENDC
                )

            if args.unused:
                print(
                    "\nThe following groups are criteria in at least one "
                    "smart group or advanced search,\n"
                    "and/or are scoped or excluded in at least one "
                    "App Store app or configuration profile:\n"
                )
                for group_name in used_groups.values():
                    print(Bcolors.OKGREEN + group_name + Bcolors.ENDC)

                print(
                    "\nThe following groups are not found in any "
                    "smart groups, advanced searches\n"
                    "App Store apps or configuration profiles:\n"
                )
                for group_id, group_name in unused_groups.items():
                    print(Bcolors.FAIL + f"[{group_id}] " + group_name + Bcolors.ENDC)

                if args.delete:
                    if actions.confirm(
                        prompt=(
                            "\nDelete all unused groups?"
                            "\n(press n to go on to confirm individually)?"
                        ),
                        default=False,
                    ):
                        delete_all = True
                    else:
                        delete_all = False
                    for group_id, group_name in unused_groups.items():
                        # prompt to delete each group in turn
                        if delete_all or actions.confirm(
                            prompt=(
                                Bcolors.OKBLUE
                                + f"Delete {group_name} (id={group_id})?"
                                + Bcolors.ENDC
                            ),
                            default=False,
                        ):
                            print(f"Deleting {group_name}...")
                            status_code = api_delete.delete_api_object(
                                jamf_url,
                                "mobile_device_group",
                                group_id,
                                token,
                                verbosity,
                            )
                            if args.slack:
                                send_slack_notification(
                                    jamf_url,
                                    args.user,
                                    slack_webhook,
                                    "mobile_device_group",
                                    group_name,
                                    "delete",
                                    status_code,
                                )
        else:
            print("\nNo groups found")
    else:
        exit("ERROR: with --groups you must supply --unused or --all.")


def get_args():
    """Parse any command line arguments"""
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--computers",
        action="store_true",
        dest="computer",
        default=[],
        help="List computers",
    )
    group.add_argument("--policies", action="store_true", help="List policies")
    group.add_argument("--packages", action="store_true", help="List packages")
    group.add_argument("--scripts", action="store_true", help="List scripts")
    group.add_argument("--ea", action="store_true", help="List extension attributes")
    group.add_argument(
        "--groups",
        "--macosgroups",
        dest="macosgroups",
        action="store_true",
        help="List Computer Groups",
    )
    group.add_argument(
        "--iosgroups", action="store_true", help="List Mobile Device Groups"
    )
    group.add_argument(
        "--macosprofiles",
        action="store_true",
        help="List Computer Configuration Profiles",
    )
    group.add_argument(
        "--iosprofiles",
        action="store_true",
        help="List Mobile Device Configuration Profiles",
    )
    group.add_argument(
        "--acs", action="store_true", help="List Advanced Computer Searches"
    )
    group.add_argument(
        "--ads", action="store_true", help="List Advanced Mobile Device Searches"
    )

    parser.add_argument(
        "--category",
        action="append",
        dest="category",
        default=[],
        help=(
            "List all policies in given category. "
            "Requires --policies. "
            "Delete available in conjunction with --delete."
        ),
    )

    parser.add_argument(
        "-n",
        "--name",
        action="append",
        dest="names",
        default=[],
        help=(
            "Give a policy name to delete. " "Requires --policies. " "Multiple allowed."
        ),
    )
    parser.add_argument(
        "--os",
        help=(
            "Restrict computer compliance to a minimum OS version. "
            "Requires --computers --all"
        ),
    )
    parser.add_argument(
        "--search",
        action="append",
        dest="search",
        default=[],
        help=(
            "List all policies or packages that start with given query. "
            "Requires --policies or --packages. "
            "Delete available in conjunction with --delete."
        ),
    )
    parser.add_argument(
        "--details",
        help="Must be used with another search argument.",
        action="store_true",
    )
    parser.add_argument(
        "--unused",
        help=(
            "Must be used with --policies, --profiles, --groups, --packages, --scripts, or --eas. "
            "Delete available in conjunction with --delete."
        ),
        action="store_true",
    )
    parser.add_argument(
        "--disabled",
        help=(
            "List all disabled policies. "
            "Must be used with --policies.. "
            "Delete available in conjunction with --delete."
        ),
        action="store_true",
    )
    parser.add_argument(
        "--delete",
        help="Must be used with another search argument.",
        action="store_true",
    )
    parser.add_argument(
        "--all",
        help=(
            "All items will be listed but no action will be taken. "
            "This is only meant for you to browse your JSS."
        ),
        action="store_true",
    )
    parser.add_argument(
        "--csv",
        default="/tmp/jamf_api_tool.csv",
        help=(
            "Path to a directory and filename to output CSVs to. Note that subdirectories will be created."
        ),
    )
    parser.add_argument("--from_csv", help="Delete from CSV file", action="store_true")
    parser.add_argument("--slack", help="Post a slack webhook", action="store_true")
    parser.add_argument("--slack_webhook", default="", help="the Slack webhook URL")
    parser.add_argument("--url", default="", help="the Jamf Pro Server URL")
    parser.add_argument(
        "--user", default="", help="a user with the rights to delete a policy"
    )
    parser.add_argument(
        "--password",
        default="",
        help="password of the user with the rights to delete a policy",
    )
    parser.add_argument(
        "--smb_url",
        default="",
        help=(
            "Path to an SMB FileShare Distribution Point, in the form "
            "smb://server/mountpoint"
        ),
    )
    parser.add_argument(
        "--smb_user",
        default="",
        help=(
            "a user with the rights to upload a package to the SMB FileShare "
            "Distribution Point"
        ),
    )
    parser.add_argument(
        "--smb_pass",
        default="",
        help=(
            "password of the user with the rights to upload a "
            "package to the SMB "
            "FileShare Distribution Point"
        ),
    )
    parser.add_argument(
        "--prefs",
        default="",
        help=(
            "full path to an AutoPkg prefs file containing "
            "JSS URL, API_USERNAME and API_PASSWORD, "
            "for example an AutoPkg preferences "
            "file which has been configured "
            "for use with JamfUploader "
            "(~/Library/Preferences/com.github.autopkg.plist) "
            "or a separate plist anywhere "
            "(e.g. ~/.com.company.jcds_upload.plist)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="print verbose output headers",
    )
    args = parser.parse_args()

    return args


def main():
    """Do the main thing here"""
    print("\n** Jamf API Tool for Jamf Pro.\n")

    # parse the command line arguments
    args = get_args()
    verbosity = args.verbose

    # grab values from a prefs file if supplied
    jamf_url, jamf_user, _, slack_webhook, enc_creds = api_connect.get_creds_from_args(
        args
    )

    if args.prefs:
        smb_url, smb_user, smb_pass = api_connect.get_smb_credentials(args.prefs)
    else:
        smb_url = ""
        smb_user = ""
        smb_pass = ""

    # repeat for optional SMB share
    # - but must supply a share path to invoke this
    if args.smb_url:
        smb_url = args.smb_url
        if args.smb_user:
            smb_user = args.smb_user
        if not smb_user:
            smb_user = input(
                f"Enter a user with read/write permissions to {smb_url} : "
            )
        if args.smb_pass:
            smb_pass = args.smb_pass
        if not smb_pass:
            if not smb_pass:
                smb_pass = getpass.getpass(f"Enter the password for '{smb_user}' : ")

    # now get the session token
    token = api_connect.get_uapi_token(jamf_url, jamf_user, enc_creds, verbosity)

    if args.slack:
        if not slack_webhook:
            print("slack_webhook value error. " "Please set it in your prefs file.")
            sys.exit()

    # computers
    if args.computer:
        handle_computers(jamf_url, token, args, slack_webhook, verbosity)

    # policies
    if args.policies:
        if args.from_csv:
            handle_policies_from_csv_data(
                jamf_url, token, args, slack_webhook, verbosity
            )
        elif args.category:
            handle_policies_in_category(jamf_url, token, args, slack_webhook, verbosity)
        else:
            handle_policies(jamf_url, token, args, slack_webhook, verbosity)

    # packages
    elif args.packages:
        handle_packages(jamf_url, token, args, slack_webhook, verbosity)

    # scripts
    elif args.scripts:
        handle_scripts(jamf_url, token, args, slack_webhook, verbosity)

    # extension attributes
    elif args.ea:
        handle_eas(jamf_url, token, args, slack_webhook, verbosity)

    # computer groupss
    elif args.macosgroups:
        handle_groups(jamf_url, token, args, slack_webhook, verbosity)

    # mobile device groups
    elif args.iosgroups:
        handle_ios_groups(jamf_url, token, args, slack_webhook, verbosity)

    # computer profiles
    elif args.macosprofiles:
        api_endpoint = "os_x_configuration_profile"
        handle_profiles(jamf_url, api_endpoint, token, args, slack_webhook, verbosity)

    # mobile device profiles
    elif args.iosprofiles:
        api_endpoint = "configuration_profile"
        handle_profiles(jamf_url, api_endpoint, token, args, slack_webhook, verbosity)

    # adavanced computer searches
    elif args.acs:
        api_endpoint = "advanced_computer_search"
        handle_advancedsearches(jamf_url, api_endpoint, token, args, verbosity)

    # adavanced mobile device searches
    elif args.ads:
        api_endpoint = "advanced_mobile_device_search"
        handle_advancedsearches(jamf_url, api_endpoint, token, args, verbosity)

    # process a name or list of names
    if args.names:
        handle_policy_list(jamf_url, token, args, slack_webhook, verbosity)

    print()


if __name__ == "__main__":
    main()
