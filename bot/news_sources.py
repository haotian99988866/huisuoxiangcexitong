"""Authorized news sources and source-safe announcement identities."""

NEWS_SOURCES = (
    ("svip", 1770576689),
    ("bx6666o", 1233397726),
    ("xinwense", 1437287068),
    ("dnyDXxinwen", 3312649794),
    ("DNY001zx", 1669737575),
    ("hrdsj3", 2937278806),
    ("vhhhh", 2062055062),
    ("vip20th", 2296480093),
)


def announcement_job_key(job):
    if job["source"] == NEWS_SOURCES[0][1]:
        return f"{job['target']}:{job['job_key']}"
    return f"{job['target']}:{job['source']}:{job['job_key']}"
