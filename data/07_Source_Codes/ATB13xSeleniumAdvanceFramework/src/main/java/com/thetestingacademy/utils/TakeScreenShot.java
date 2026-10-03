package com.thetestingacademy.utils;

import io.qameta.allure.Allure;
import org.apache.commons.io.FileUtils;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.TakesScreenshot;
import org.openqa.selenium.WebDriver;

import java.io.File;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Calendar;

import static com.thetestingacademy.driver.DriverManager.getDriver;

public class TakeScreenShot {

    public void takeScreenshot(String name){
        WebDriver driver = getDriver();

        String methodName = "Screenshot_"+name;

        Calendar calendar = Calendar.getInstance();
        SimpleDateFormat formatter = new SimpleDateFormat("dd_MM_yyyy_hh_mm_ss");


        if (driver != null) {
            File scrFile = ((TakesScreenshot) driver).getScreenshotAs(OutputType.FILE);
            try {
                String screenshotPath = "failure_screenshots/" + methodName + "_" +
                        formatter.format(calendar.getTime()) + ".png";
                FileUtils.copyFile(scrFile, new File(screenshotPath));

                // Add screenshot link to TestNG report
                Allure.addAttachment("Screenshot", "image/png", screenshotPath, "png");
            } catch (IOException e) {
                e.printStackTrace();
            }
        }

    }
}
