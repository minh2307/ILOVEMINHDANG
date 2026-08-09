#!/bin/bash
while read url; do
  if [ -n "$url" ]; then
    echo "Creating job for $url"
    .venv/bin/python main.py create-job --url "$url"
  fi
done < List_Facebook.txt
