# Automated Class Scheduling System

A Django-based robust backend API for generating automated, conflict-free class routines for university departments.

## Features
- **Automated Routing Algorithm:** Generates conflict-free schedules based on teacher, room, and batch constraints.
- **Dynamic Room Allocation:** Matches course capacity and type (Theory/Lab) with appropriate rooms.
- **Admin Overrides:** Allows administrators to fix specific days, time slots, or rooms for specific courses.
- **Interactive API Documentation:** Integrated with Swagger UI (`drf-yasg`) for seamless API testing.

## Tech Stack
- Backend: Django, Django REST Framework (DRF)
- Database: PostgreSQL
- Documentation: Swagger UI
