"""Appended to provider.py when uploaded as a self-contained OCI stock helper."""


def sandbox_main():
    import sys
    from pathlib import Path

    request_file, output_file = map(Path, sys.argv[1:3])
    request = json.loads(request_file.read_text())
    operation, value = request["operation"], request["value"]
    if operation == "search":
        payload = search_stocks(value)
    elif operation == "quote":
        payload = get_quote(value)
    else:
        raise ValueError("Unsupported stock operation")
    result = {"requestId": request["requestId"], "operation": operation, "value": value,
              "payload": payload, "completedAt": datetime.now(timezone.utc).isoformat()}
    # Unique per-request files; never overwrite a previous result.
    with output_file.open("x") as handle:
        json.dump(result, handle, allow_nan=False)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    sandbox_main()
