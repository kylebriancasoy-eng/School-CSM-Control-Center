FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 schoolcsm
WORKDIR /service
COPY registration_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt
COPY registration_service ./registration_service
RUN mkdir -p /var/lib/school-csm-registration && \
    chown -R schoolcsm:schoolcsm /var/lib/school-csm-registration /service

USER schoolcsm
ENV SCHOOL_CSM_REGISTRATION_DATABASE=/var/lib/school-csm-registration/registration.sqlite3
EXPOSE 8000
CMD ["uvicorn", "registration_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
