import secrets
import string


JOB_CODE_LENGTH = 10

JOB_CODE_ALPHABET = (
    string.ascii_lowercase
    + string.digits
)


def generate_job_code() -> str:
    return "".join(
        secrets.choice(JOB_CODE_ALPHABET)
        for _ in range(JOB_CODE_LENGTH)
    )
