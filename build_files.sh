echo "BUILD START"
docker-compose up --build
docker-compose exec web python manage.py migrate
echo "BUILD END"