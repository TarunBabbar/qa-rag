package com.thetestingacademy.tests.vwo.pageFactoryTC;


import com.thetestingacademy.base.CommonToAllTest;
import com.thetestingacademy.driver.DriverManager;
import com.thetestingacademy.pages.pageFactory.LoginPage_PF;
import com.thetestingacademy.utils.PropertiesReader;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.testng.Assert;
import org.testng.annotations.Test;



public class TestVWOLogin_PF extends CommonToAllTest {

    private static final Logger logger = LogManager.getLogger(TestVWOLogin_PF.class);


    @Test
    public void testLoginNegativeVWO_PF() {
        logger.info("Starting the Testcases Page Factory");


        LoginPage_PF loginPage_PF = new LoginPage_PF(DriverManager.getDriver());
        String error_msg = loginPage_PF.loginToVWOInvalidCreds();


        logger.info("Error msg I got "+ error_msg);
        logger.info("Finished the Testcases Page Factory");


        Assert.assertEquals(error_msg, PropertiesReader.readKey("error_message"));
    }

}
