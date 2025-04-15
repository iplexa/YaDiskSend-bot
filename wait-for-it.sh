#!/usr/bin/env sh
# Используем стандартный скрипт из репозитория GitHub
# Источник: https://github.com/vishnubob/wait-for-it/blob/master/wait-for-it.sh

TIMEOUT=15
QUIET=0

while getopts "t:q" OPTION; do
  case $OPTION in
    t) TIMEOUT=$OPTARG;;
    q) QUIET=1;;
  esac
done

shift $(($OPTIND - 1))
HOST=$1
PORT=$2

for i in `seq $TIMEOUT` ; do
  nc -z $HOST $PORT > /dev/null 2>&1
  result=$?
  if [ $result -eq 0 ]; then
    exit 0
  fi
  sleep 1
done

echo "Не удалось подключиться к $HOST:$PORT за $TIMEOUT секунд" >&2
exit 1