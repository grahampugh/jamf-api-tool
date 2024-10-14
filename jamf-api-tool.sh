#!/bin/bash

: <<DOC
A wrapper script for running the jamf_api_tool.py script
DOC

###########
## USAGE ##
###########

usage() {
    echo "
Usage: 
./jamf-api-tool.sh [--help] [arguments]

A wrapper script to run jamf_api_tool.py supplying the required credentials.

Arguments:
    --prefs <path>          Inherit AutoPkg prefs file provided by the full path to the file
    -v[vvv]                 Set value of verbosity
    --url <JSS_URL>         The Jamf Pro URL
    --user <API_USERNAME>   The API username
    --pass <API_PASSWORD>   The API user's password

"
}

##############
## DEFAULTS ##
##############

# this folder
DIR=$(dirname "$0")
tool_directory="$DIR"
tool="jamf_api_tool.py"
tmp_prefs="${HOME}/Library/Preferences/jamf-api-tool.plist"
autopkg_prefs="${HOME}/Library/Preferences/com.github.autopkg.plist"

###############
## ARGUMENTS ##
###############

args=()

while test $# -gt 0 ; do
    case "$1" in
        --prefs)
            shift
            autopkg_prefs="$1"
            ;;
        -v*)
            args+=("$1")
            ;;
        --url) 
            shift
            url="$1"
            ;;
        --user*)  
            ## allows --user or --username
            shift
            user="$1"
            ;;
        --pass*)  
            ## allows --pass or --password
            shift
            password="$1"
            ;;
        --csv)  
            shift
            csv="$1"
            ;;
        --slack)  
            shift
            slack_webhook_url="$1"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            args+=("$1")
            ;;
    esac
    shift
done
echo

if [[ "$url" && "$user" && "$password" ]]; then
    # write temp prefs file
    /usr/bin/defaults write "$tmp_prefs" JSS_URL "$url"
    /usr/bin/defaults write "$tmp_prefs" API_USERNAME "$user"
    /usr/bin/defaults write "$tmp_prefs" API_PASSWORD "$password"
    args+=("--prefs")
    args+=("$tmp_prefs")
elif [[ -f "$autopkg_prefs" ]]; then
    args+=("--prefs")
    args+=("$autopkg_prefs")
else
    echo "No credentials supplied"
    exit 1
fi

if [[ $csv ]]; then
    args+=("--csv")
    args+=("$csv")
fi

if [[ $slack_webhook_url ]]; then
    /usr/bin/defaults write "$tmp_prefs" SLACK_WEBHOOK "$slack_webhook_url"
    args+=("--slack")
fi

###############
## MAIN BODY ##
###############

# Ensure PYHTONPATH includes the AutoPkg libraries
if [[ -d "/Library/AutoPkg" ]]; then
    export PYTHONPATH="/Library/AutoPkg"
else
    echo "ERROR: AutoPkg is not installed"
    exit 1
fi

echo 
# Run the script and output to stdout
/Library/AutoPkg/Python3/Python.framework/Versions/Current/bin/python3 "$tool_directory/$tool" "${args[@]}" 
