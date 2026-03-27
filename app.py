import streamlit as st
import mysql.connector
import pandas as pd
import plotly.express as px
import ollama
from datetime import date
import re

st.set_page_config(page_title="NutriFit", page_icon="🥗", layout="wide")

# ---------------- DATABASE ----------------
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Root",
    database="nutrifit"
)
cursor = db.cursor()

st.title("🥗 NutriFit")

# ---------------- AI ENGINE ----------------
def ai_generate(prompt):

    system_prompt = """
You are a professional nutritionist.

Rules:
• Answer clearly
• Use bullet points if advice
• If estimating food nutrition return:

Calories: number
Protein: number
Carbs: number
Fat: number
"""

    response = ollama.chat(
        model="phi3",
        messages=[
            {"role":"system","content":system_prompt},
            {"role":"user","content":prompt}
        ],
        options={
            "num_predict":200,
            "temperature":0.2
        }
    )

    return response["message"]["content"]


# -------- PARSE AI NUTRITION --------
def extract_nutrition(text):

    calories = re.search(r'Calories:\s*(\d+)', text)
    protein = re.search(r'Protein:\s*(\d+)', text)
    carbs = re.search(r'Carbs:\s*(\d+)', text)
    fat = re.search(r'Fat:\s*(\d+)', text)

    return (
        int(calories.group(1)),
        int(protein.group(1)),
        int(carbs.group(1)),
        int(fat.group(1))
    )

menu = ["Login","Signup","Dashboard","AI Nutrition Assistant"]
choice = st.sidebar.selectbox("Menu",menu)

# ---------------- SIGNUP ----------------
if choice == "Signup":

    username = st.text_input("Username")
    password = st.text_input("Password",type="password")

    if st.button("Signup"):

        cursor.execute(
            "INSERT INTO users(username,password) VALUES(%s,%s)",
            (username,password)
        )

        db.commit()
        st.success("Account created")

# ---------------- LOGIN ----------------
elif choice == "Login":

    username = st.text_input("Username")
    password = st.text_input("Password",type="password")

    if st.button("Login"):

        cursor.execute(
            "SELECT user_id FROM users WHERE username=%s AND password=%s",
            (username,password)
        )

        result = cursor.fetchone()

        if result:
            st.session_state["user"] = result[0]
            st.success("Login successful")
        else:
            st.error("Invalid credentials")

# ---------------- DASHBOARD ----------------
elif choice == "Dashboard":

    if "user" not in st.session_state:
        st.warning("Please login first")
        st.stop()

    user_id = st.session_state["user"]

    page = st.sidebar.selectbox(
        "Dashboard Menu",
        ["Add Member","Add Meal","Daily Summary","Weekly Summary"]
    )

# ---------------- ADD MEMBER ----------------
    if page == "Add Member":

        name = st.text_input("Member Name")
        age = st.number_input("Age",0,120)
        gender = st.selectbox("Gender",["Male","Female"])
        weight = st.number_input("Weight")
        height = st.number_input("Height")

        if st.button("Add Member"):

            cursor.execute(
                "INSERT INTO members(user_id,name,age,gender,weight,height) VALUES(%s,%s,%s,%s,%s,%s)",
                (user_id,name,age,gender,weight,height)
            )

            db.commit()
            st.success("Member added")

# ---------------- ADD MEAL ----------------
    elif page == "Add Meal":

        st.subheader("Add Meal")

        cursor.execute(
            "SELECT member_id,name FROM members WHERE user_id=%s",
            (user_id,)
        )

        members = cursor.fetchall()
        member_dict = {m[1]:m[0] for m in members}

        member_name = st.selectbox("Select Member",list(member_dict.keys()))
        member_id = member_dict[member_name]

        meal_type = st.selectbox(
            "Meal Type",
            ["Breakfast","Lunch","Snacks","Dinner"]
        )

        meal_date = st.date_input("Date",date.today())

        food_name = st.text_input("Enter Food Name")

        quantity = st.number_input("Quantity",1.0)

        if st.button("Add Meal"):

            cursor.execute(
                "SELECT food_id FROM food_items WHERE food_name=%s",
                (food_name,)
            )

            food = cursor.fetchone()

            # ---- FOOD EXISTS ----
            if food:
                food_id = food[0]

            # ---- AI ESTIMATE ----
            else:

                st.info("Food not found. Estimating nutrition using AI...")

                ai_result = ai_generate(
                    f"Estimate nutrition for {food_name} per 100g"
                )

                calories,protein,carbs,fat = extract_nutrition(ai_result)

                cursor.execute(
                    """
                    INSERT INTO food_items
                    (food_name,calories,protein,carbs,fat)
                    VALUES(%s,%s,%s,%s,%s)
                    """,
                    (food_name,calories,protein,carbs,fat)
                )

                db.commit()

                cursor.execute(
                    "SELECT food_id FROM food_items WHERE food_name=%s",
                    (food_name,)
                )

                food_id = cursor.fetchone()[0]

            cursor.execute(
                "INSERT INTO meals(member_id,meal_type,meal_date) VALUES(%s,%s,%s)",
                (member_id,meal_type,meal_date)
            )

            meal_id = cursor.lastrowid

            cursor.execute(
                "INSERT INTO meal_food(meal_id,food_id,quantity) VALUES(%s,%s,%s)",
                (meal_id,food_id,quantity)
            )

            db.commit()

            st.success("Meal added successfully")

# ---------------- DAILY SUMMARY ----------------
    elif page == "Daily Summary":

        st.subheader("Food History")

        cursor.execute("""
        SELECT m.meal_type,f.food_name,mf.quantity
        FROM meal_food mf
        JOIN food_items f ON mf.food_id=f.food_id
        JOIN meals m ON mf.meal_id=m.meal_id
        JOIN members mem ON m.member_id=mem.member_id
        WHERE mem.user_id=%s
        AND m.meal_date=CURDATE()
        """,(user_id,))

        history = cursor.fetchall()

        if history:
            st.dataframe(pd.DataFrame(
                history,
                columns=["Meal","Food","Quantity"]
            ))

        st.subheader("Nutrition Summary")

        cursor.execute("""
        SELECT f.food_name,f.calories,f.protein,f.carbs,f.fat,mf.quantity
        FROM meal_food mf
        JOIN food_items f ON mf.food_id=f.food_id
        JOIN meals m ON mf.meal_id=m.meal_id
        JOIN members mem ON m.member_id=mem.member_id
        WHERE mem.user_id=%s
        AND m.meal_date=CURDATE()
        """,(user_id,))

        data = cursor.fetchall()

        if data:

            df = pd.DataFrame(
                data,
                columns=["Food","Calories","Protein","Carbs","Fat","Quantity"]
            )

            df["Calories Total"] = df["Calories"] * df["Quantity"]

            total_calories = df["Calories Total"].sum()
            total_protein = (df["Protein"] * df["Quantity"]).sum()
            total_carbs = (df["Carbs"] * df["Quantity"]).sum()
            total_fat = (df["Fat"] * df["Quantity"]).sum()

            col1,col2,col3,col4 = st.columns(4)

            col1.metric("Calories",int(total_calories))
            col2.metric("Protein",int(total_protein))
            col3.metric("Carbs",int(total_carbs))
            col4.metric("Fat",int(total_fat))

            # CALORIE CHART
            fig = px.bar(df,x="Food",y="Calories Total")
            st.plotly_chart(fig)

            # MACRO PIE
            macro_df = pd.DataFrame({
                "Macro":["Protein","Carbs","Fat"],
                "Amount":[total_protein,total_carbs,total_fat]
            })

            fig2 = px.pie(macro_df,names="Macro",values="Amount")
            st.plotly_chart(fig2)

            # AI RECOMMENDATION
            st.subheader("AI Meal Recommendation")

            if st.button("Suggest What I Should Eat Next"):

                prompt = f"""
Today's intake:
Calories: {total_calories}
Protein: {total_protein}
Carbs: {total_carbs}
Fat: {total_fat}

Recommend a healthy next meal.
"""

                st.write(ai_generate(prompt))

# ---------------- WEEKLY SUMMARY ----------------
    elif page == "Weekly Summary":

        st.subheader("Weekly Nutrition Summary")

        cursor.execute("""
        SELECT m.meal_date,f.calories,f.protein,f.carbs,f.fat,mf.quantity
        FROM meal_food mf
        JOIN food_items f ON mf.food_id=f.food_id
        JOIN meals m ON mf.meal_id=m.meal_id
        JOIN members mem ON m.member_id=mem.member_id
        WHERE mem.user_id=%s
        AND m.meal_date >= CURDATE() - INTERVAL 7 DAY
        """,(user_id,))

        data = cursor.fetchall()

        if data:

            df = pd.DataFrame(
                data,
                columns=["Date","Calories","Protein","Carbs","Fat","Quantity"]
            )

            df["Calories Total"] = df["Calories"] * df["Quantity"]

            weekly_cal = df["Calories Total"].sum()
            weekly_pro = (df["Protein"] * df["Quantity"]).sum()
            weekly_car = (df["Carbs"] * df["Quantity"]).sum()
            weekly_fat = (df["Fat"] * df["Quantity"]).sum()

            col1,col2,col3,col4 = st.columns(4)

            col1.metric("Weekly Calories",int(weekly_cal))
            col2.metric("Weekly Protein",int(weekly_pro))
            col3.metric("Weekly Carbs",int(weekly_car))
            col4.metric("Weekly Fat",int(weekly_fat))

            # DAILY TREND
            daily = df.groupby("Date")["Calories Total"].sum().reset_index()

            fig1 = px.line(daily,x="Date",y="Calories Total",markers=True)
            st.plotly_chart(fig1)

            # MACRO PIE
            macro_df = pd.DataFrame({
                "Macro":["Protein","Carbs","Fat"],
                "Amount":[weekly_pro,weekly_car,weekly_fat]
            })

            fig2 = px.pie(macro_df,names="Macro",values="Amount")
            st.plotly_chart(fig2)

# ---------------- AI ASSISTANT ----------------
elif choice == "AI Nutrition Assistant":

    question = st.text_input("Ask a nutrition question")

    if question:
        st.write(ai_generate(question))