# Expense Tracker Web Application

## Project Overview

The Expense Tracker is a web-based application developed using Python Flask that helps users manage their personal finances efficiently. Users can record income and expenses, categorize spending, calculate balances, and monitor financial activity through a dashboard.

---

# Features

## Authentication System
- User Registration
- User Login
- Logout
- Password Hashing
- Session Management
- New user creation option
- Access to new user based on approval from admin

---

## Dashboard
The dashboard displays:
- Total Income
- Total Expenses
- Remaining Balance
- Recent Transactions
- Unecessary expense track
- Money Saved
- Track indvidual expense
- Track Family expense

---

## Expense Management
Users can:
- Add Expenses
- Edit Expenses
- Delete Expenses
- View Expense History
- Track unecessary expense using Machine learning methods and display
- Track the money saved by avoid cab or any other means 
- Track indvidual expense
- Track Family expense

### Expense Fields
- Amount
- Category
- Description
- Date

---

## Income Management
Users can:
- Add Income
- View Income History

---

## Categories
The system includes categories such as:
- Food
- Travel
- Shopping
- Bills
- Entertainment
- Health
- Education 
- Money Saved
- Unecessary spending
- Hotel
- Movie
- other

---

# Technologies Used

## Backend
- Python
- Flask

## Frontend
- HTML
- CSS
- JavaScript

## Database
- SQLite / MySQL

## Charts
- Chart.js

---

# Project Structure

```plaintext
expense_tracker/
│
├── app.py
├── templates/
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│
├── static/
│   ├── css/
│   ├── js/
│   └── images/
│
├── database/
│
├── models/
│
├── routes/
│
└── requirements.txt
```

---

# Database Tables

## Users Table

| Field | Type |
|---|---|
| id | Integer |
| username | Text |
| email | Text |
| password | Text |

---

## Expenses Table

| Field | Type |
|---|---|
| id | Integer |
| user_id | Integer |
| amount | Float |
| category | Text |
| description | Text |
| date | Date |

---

## Income Table

| Field | Type |
|---|---|
| id | Integer |
| user_id | Integer |
| amount | Float |
| source | Text |
| date | Date |

---

# Application Workflow

## Step 1 — User Registration
The user creates an account using:
- Username
- Email
- Password

The password is securely hashed before storing in the database.

---

## Step 2 — User Login
The user logs into the system using email and password.

The application verifies:
- User existence
- Password correctness

If valid, the dashboard opens.

---

## Step 3 — Dashboard Display
The dashboard calculates and displays:
- Total Income
- Total Expenses
- Remaining Balance

### Balance Formula

Balance = Total Income - Total Expense

---

## Step 4 — Add Expense
The user enters:
- Amount
- Category
- Description
- Date

The data is stored in the database.

---

## Step 5 — View Expenses
Users can:
- View all expenses
- Edit records
- Delete records

---

# CRUD Operations

| Operation | Purpose |
|---|---|
| Create | Add Expense |
| Read | View Expense |
| Update | Edit Expense |
| Delete | Remove Expense |

---

# Backend Functionalities

## Flask Routes
The application contains routes for:
- Home
- Login
- Register
- Dashboard
- Add Expense
- Edit Expense
- Delete Expense
- Logout

---

## Form Validation
The backend validates:
- Empty fields
- Invalid data
- Duplicate users

---

## Session Management
Flask sessions keep users logged into the application securely.

---

# User Interface

## UI Design Includes
- Responsive Layout
- Sidebar Navigation
- Dashboard Cards
- Mobile-Friendly Design
- Clean User Experience

---

# Additional Features

## Expense Charts
Using Chart.js:
- Monthly Expense Graph
- Category-wise Expense Graph

---

## Search and Filter
Users can:
- Search expenses
- Filter by category
- Filter by date

---

## Dark Mode
The application supports dark mode for better user experience.

---

## PDF Export
Users can download expense reports as PDF files.

---

# Learning Outcomes

This project helps developers understand:
- Flask Development
- Authentication Systems
- Database Integration
- CRUD Operations
- Dashboard Design
- Backend Logic
- Financial Calculations
- Real-world Web Application Structure

---

# Future Enhancements

- AI-based spending analysis
- Email notifications
- Cloud database integration
- Multi-user support
- Budget prediction system

---

# Conclusion

The Expense Tracker is a powerful real-world Flask project that helps users manage finances while teaching important web development concepts such as authentication, database handling, CRUD operations, dashboard creation, and backend logic implementation.