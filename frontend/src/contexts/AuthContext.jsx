import React, { createContext, useContext, useState , useEffect} from 'react';
import axios from 'axios';
import {jwtDecode} from 'jwt-decode';

const AuthContext = createContext(undefined);

export  function AuthProvider({ children }) {
  const [token,setToken]= useState(localStorage.getItem("token")||"");
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(false);
  
  useEffect(() => {
    if (token) localStorage.setItem("token", token);
    else localStorage.removeItem("token");
  }, [token]);

   // Fetch user info when token changes
   useEffect(() => {
    const fetchUser = async () => {
      try {
        const decoded = jwtDecode(token);
        const useremail = decoded.useremail;
        const res = await axios.get(`http://localhost:8000/login/${useremail}`, {
          headers: { Authorization: `Bearer ${token}` } // pass JWT if needed
        });
        setUser({
          email: useremail,
          name: res.data.username
        });
      } catch (error) {
        console.log(error);
        setUser(null);
        setToken(""); // clear invalid token
      }
    };

    if (token) fetchUser();
    else setUser(null);
  }, [token]);


  // Signup request
  const signUp = async (email, password, name) => {
    setLoading(true);
    const form = {
      "useremail": email,
      "userpassword": password,
      "username": name,
    };

    try {
      const res = await axios.post("http://localhost:8000/signup", form);
      alert(res.data.msg);
      setToken(res.data.token); 
      return { error: null };
    } catch (error) {
      if (error.response) {
        return { error: error.response.data.detail };
      }
      return { error: "Something went wrong" };
    } finally {
      setLoading(false);
    }
  };

  // Signin request
  const signIn = async (email, password) => {
    setLoading(true);
    const form = {
      useremail:email,
      userpassword:password
    }
    
    try{
      const res= await axios.post("http://localhost:8000/login",form);
      alert(res.data.msg);
      setToken(res.data.token); // <--- save JWT
      return{error:null};
    }catch(error){
      setLoading(false);
      if(error.response){
        return{error:error.response.data.detail};
      }
      return { error: "Something went wrong" };
    }finally {
      setLoading(false);
    }
  };

  const signOut = async () => {
    setUser(null);
    setToken("");
  };

  const value = {
    user,
    loading,
    signUp,
    signIn,
    signOut,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export  function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
